// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/**
 * GET /api/suites/[suite_id]/config
 *
 * Returns a small, wizard-friendly summary of an existing suite's authored
 * configuration, read from its most recent run's `eval_config.yaml`. The New
 * evaluation wizard uses this to pre-fill the "existing suite" flow (behavior,
 * application context, system prompt, target kind) when launched from a suite's
 * "Run evaluation" button via `/new?suite=<id>`.
 */

import { json } from '@sveltejs/kit';
import fs from 'node:fs';
import path from 'node:path';
import { parse as parseYaml } from 'yaml';
import { isSafeArtifactId, suiteDirPath } from '$lib/server/artifacts.js';
import type { RequestHandler } from './$types.js';

const RUN_EVAL_CONFIG_FILE = 'eval_config.yaml';

/** Most recently modified run dir under `suiteDir` that has an eval_config.yaml. */
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

/** Run dir names under `suiteDir` (excludes the shared `artifacts` cache dir). */
function listRunIds(suiteDir: string): string[] {
	let entries: fs.Dirent[];
	try {
		entries = fs.readdirSync(suiteDir, { withFileTypes: true });
	} catch {
		return [];
	}
	return entries
		.filter((e) => e.isDirectory() && e.name !== 'artifacts' && isSafeArtifactId(e.name))
		.map((e) => e.name);
}

/**
 * Next free `v<N>` run id given the existing run ids. Re-running an existing
 * suite should not collide with a prior run, so we auto-increment past the
 * highest existing `v`-numbered run (v1 -> v2). Suites with no v-style runs
 * start at v1.
 */
function nextRunId(existingRunIds: string[]): string {
	const vNums = existingRunIds
		.map((id) => /^v(\d+)$/.exec(id))
		.filter((m): m is RegExpExecArray => m !== null)
		.map((m) => Number(m[1]));
	return vNums.length > 0 ? `v${Math.max(...vNums) + 1}` : 'v1';
}

function asString(value: unknown): string {
	return typeof value === 'string' ? value : '';
}

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

export const GET: RequestHandler = async ({ params }) => {
	const suiteId = params.suite_id ?? '';
	if (!suiteId || !isSafeArtifactId(suiteId)) {
		return json({ error: 'A valid suite id is required.' }, { status: 400 });
	}

	const suiteDir = suiteDirPath(suiteId);
	if (!fs.existsSync(suiteDir)) {
		return json({ error: `Suite "${suiteId}" was not found.` }, { status: 404 });
	}

	const existingRunIds = listRunIds(suiteDir);
	const suggestedRunId = nextRunId(existingRunIds);

	const empty = {
		suiteId,
		behaviorName: null as string | null,
		context: '',
		systemPrompt: '',
		evaluationTarget: 'model' as 'model' | 'agent',
		existingRunIds,
		nextRunId: suggestedRunId
	};

	const configPath = findLatestRunConfig(suiteDir);
	if (!configPath) {
		return json(empty);
	}

	let config: Record<string, unknown>;
	try {
		const parsed = parseYaml(fs.readFileSync(configPath, 'utf-8'));
		config = asRecord(parsed);
	} catch {
		return json(empty);
	}

	const behaviorName = asString(asRecord(config.behavior).name) || null;
	const context = asString(config.context);
	const target = asRecord(asRecord(asRecord(config.pipeline).inference).target);
	const systemPrompt = asString(target.system_prompt);
	// `target.model` means a hosted-model target; anything else (agent/callable)
	// is treated as an agent target by the wizard.
	const evaluationTarget: 'model' | 'agent' = target.model ? 'model' : target.agent || target.callable ? 'agent' : 'model';

	return json({
		suiteId,
		behaviorName,
		context,
		systemPrompt,
		evaluationTarget,
		existingRunIds,
		nextRunId: suggestedRunId
	});
};
