// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { json } from '@sveltejs/kit';
import { getActiveRuns } from '$lib/server/runner.js';
import {
	normalizeWizardPayload,
	writeRunConfigFiles,
	spawnP2mRun,
	runDirExists,
	WizardValidationError,
	RunConflictError,
	SpawnError
} from '$lib/server/run-spawn.js';
import type { RequestHandler } from './$types.js';

/**
 * GET /api/runs
 *
 * Lists currently-running evaluations. Used by the landing page and the
 * monitor page to refresh status without polling individual manifests.
 */
export const GET: RequestHandler = async () => {
	const runs = getActiveRuns().map((r) => ({
		suiteId: r.suiteId,
		runId: r.runId,
		status: r.status,
		startedAt: r.startedAt,
		currentStage: r.currentStage,
		stages: r.stages
	}));
	return json(runs);
};

/**
 * POST /api/runs
 *
 * Create a new evaluation run from a wizard payload.
 *
 * Flow:
 *   1. Parse + normalize the JSON body (400 on any wizard-side error).
 *   2. Refuse with 409 if a run with the same suite/run already exists.
 *   3. mkdir the run directory atomically; write eval_config.yaml (behavior
 *      spec lives inline in behavior.description — no separate spec file).
 *   4. Spawn `assert-eval run` detached; wait for the OS to acknowledge the spawn
 *      so a missing binary surfaces as 500 (not 200 + forever-pending monitor).
 *   5. Return { suiteId, runId, pid, warnings } so the wizard can navigate
 *      to /suite/<suiteId>/<runId>/monitor and start polling status.
 */
export const POST: RequestHandler = async ({ request }) => {
	let raw: unknown;
	try {
		raw = await request.json();
	} catch (err) {
		return json(
			{ error: 'Request body must be valid JSON.', details: [(err as Error).message] },
			{ status: 400 }
		);
	}

	let normalized;
	try {
		normalized = normalizeWizardPayload(raw);
	} catch (err) {
		if (err instanceof WizardValidationError) {
			return json(
				{ error: 'Wizard payload validation failed.', details: err.details },
				{ status: 400 }
			);
		}
		throw err;
	}

	// Pre-check for a collision so we can surface 409 before the mkdir race.
	// The mkdir in writeRunConfigFiles is still the authoritative atomic lock;
	// this check just gives a nicer error message in the common case.
	if (runDirExists(normalized.suite, normalized.run)) {
		return json(
			{
				error: 'Run already exists.',
				details: [
					`A run already exists at artifacts/results/${normalized.suite}/${normalized.run}. ` +
						'Pick a different run ID or remove the existing run directory.'
				]
			},
			{ status: 409 }
		);
	}

	let written;
	try {
		written = writeRunConfigFiles(normalized);
	} catch (err) {
		if (err instanceof RunConflictError) {
			return json({ error: 'Run already exists.', details: [err.message] }, { status: 409 });
		}
		return json(
			{
				error: 'Failed to write run configuration files.',
				details: [(err as Error).message ?? String(err)]
			},
			{ status: 500 }
		);
	}

	let spawned;
	try {
		spawned = await spawnP2mRun(written);
	} catch (err) {
		const message = err instanceof SpawnError ? err.message : (err as Error).message ?? String(err);
		return json(
			{
				error: 'Failed to start the assert-eval runner.',
				details: [message],
				// Surface the partially-written run dir so the user can inspect or clean up.
				runDir: written.runDir
			},
			{ status: 500 }
		);
	}

	return json(
		{
			suiteId: normalized.suite,
			runId: normalized.run,
			pid: spawned.pid,
			warnings: normalized.warnings,
			runDir: written.runDir,
			configPath: written.configPath
		},
		{ status: 200 }
	);
};
