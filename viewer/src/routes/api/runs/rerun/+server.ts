// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/**
 * POST /api/runs/rerun
 *
 * Re-runs an evaluation for an EXISTING suite using its most recent run's
 * authored config (`eval_config.yaml`). This powers the "Run evaluation"
 * button on the suite (taxonomy) page: it reuses the suite's existing
 * taxonomy/test-set/target config — including any edits made to taxonomy.json —
 * rather than sending the user back to the blank `/new` wizard.
 *
 * Flow:
 *   1. Validate suiteId and locate the suite directory.
 *   2. Find the newest prior run that has an `eval_config.yaml` and read it.
 *   3. Allocate a fresh run id, create its directory (mkdir is the lock).
 *   4. Re-point the config at { suite, run } and write it into the new run dir.
 *   5. Spawn the runner detached and return { suiteId, runId, pid }.
 */

import { json } from '@sveltejs/kit';
import fs from 'node:fs';
import path from 'node:path';
import { parse as parseYaml, stringify as stringifyYaml } from 'yaml';
import { isSafeArtifactId, runDirPath, suiteDirPath } from '$lib/server/artifacts.js';
import { spawnAssertAiRun, SpawnError, type WrittenRun } from '$lib/server/run-spawn.js';
import type { RequestHandler } from './$types.js';

const RUN_EVAL_CONFIG_FILE = 'eval_config.yaml';
const RUN_LOG_FILE = 'runner.log';
const RUN_PID_FILE = 'runner.pid';

/** Generate a collision-resistant, filesystem-safe run id. */
function newRunId(): string {
	const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
	return `rerun-${stamp}`;
}

/**
 * Find the most recently modified run directory under `suiteDir` that contains
 * an authored `eval_config.yaml`. Returns the absolute path to that file, or
 * null when no prior run has one.
 */
function findLatestRunConfig(suiteDir: string): string | null {
	let entries: fs.Dirent[];
	try {
		entries = fs.readdirSync(suiteDir, { withFileTypes: true });
	} catch {
		return null;
	}
	let best: { path: string; mtimeMs: number } | null = null;
	for (const entry of entries) {
		if (!entry.isDirectory()) continue;
		// Skip the content-addressed artifact store and unsafe names.
		if (entry.name === 'artifacts' || !isSafeArtifactId(entry.name)) continue;
		const configPath = path.join(suiteDir, entry.name, RUN_EVAL_CONFIG_FILE);
		let stat: fs.Stats;
		try {
			stat = fs.statSync(configPath);
		} catch {
			continue;
		}
		if (!stat.isFile()) continue;
		if (!best || stat.mtimeMs > best.mtimeMs) {
			best = { path: configPath, mtimeMs: stat.mtimeMs };
		}
	}
	return best ? best.path : null;
}

export const POST: RequestHandler = async ({ request }) => {
	let body: { suiteId?: unknown };
	try {
		body = await request.json();
	} catch {
		return json({ error: 'Request body must be valid JSON.' }, { status: 400 });
	}

	const suiteId = typeof body.suiteId === 'string' ? body.suiteId.trim() : '';
	if (!suiteId || !isSafeArtifactId(suiteId)) {
		return json({ error: 'A valid "suiteId" is required.' }, { status: 400 });
	}

	const suiteDir = suiteDirPath(suiteId);
	if (!fs.existsSync(suiteDir)) {
		return json({ error: `Suite "${suiteId}" was not found.` }, { status: 404 });
	}

	const sourceConfigPath = findLatestRunConfig(suiteDir);
	if (!sourceConfigPath) {
		return json(
			{
				error:
					'This suite has no prior run with an eval_config.yaml to re-run. Start it from the New evaluation wizard first.'
			},
			{ status: 400 }
		);
	}

	let configObject: Record<string, unknown>;
	try {
		const parsed = parseYaml(fs.readFileSync(sourceConfigPath, 'utf-8'));
		configObject = parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
	} catch (err) {
		return json(
			{ error: 'The existing run config could not be parsed.', details: [String(err)] },
			{ status: 400 }
		);
	}

	const runId = newRunId();
	if (!isSafeArtifactId(runId)) {
		return json({ error: 'Failed to generate a safe run id.' }, { status: 500 });
	}

	const runDir = runDirPath(suiteId, runId);
	try {
		fs.mkdirSync(runDir, { recursive: false });
	} catch (err) {
		const code = (err as NodeJS.ErrnoException).code;
		if (code === 'EEXIST') {
			return json({ error: `Run "${runId}" already exists.` }, { status: 409 });
		}
		return json(
			{ error: 'Failed to create the run directory.', details: [String(err)] },
			{ status: 500 }
		);
	}

	// Re-point the reused config at the new suite/run so the runner writes here.
	configObject.suite = suiteId;
	configObject.run = runId;

	const configPath = path.join(runDir, RUN_EVAL_CONFIG_FILE);
	try {
		fs.writeFileSync(configPath, stringifyYaml(configObject, { lineWidth: 0 }), 'utf-8');
	} catch (err) {
		return json(
			{ error: 'Failed to write the run config.', details: [String(err)] },
			{ status: 500 }
		);
	}

	const written: WrittenRun = {
		runDir,
		configPath,
		logPath: path.join(runDir, RUN_LOG_FILE),
		pidPath: path.join(runDir, RUN_PID_FILE)
	};

	try {
		const spawned = await spawnAssertAiRun(written);
		return json({ suiteId, runId, pid: spawned.pid, runDir }, { status: 200 });
	} catch (err) {
		const message =
			err instanceof SpawnError ? err.message : ((err as Error).message ?? String(err));
		return json(
			{ error: 'Failed to start the evaluation runner.', details: [message], suiteId, runId },
			{ status: 500 }
		);
	}
};
