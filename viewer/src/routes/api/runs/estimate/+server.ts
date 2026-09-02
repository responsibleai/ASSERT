// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { json } from '@sveltejs/kit';
import {
	estimateAssertAiRun,
	EstimateError,
	normalizeWizardPayload,
	WizardValidationError
} from '$lib/server/run-spawn.js';
import type { RequestHandler } from './$types.js';

/**
 * POST /api/runs/estimate
 *
 * Validate the same payload used to create a run, then execute the local,
 * read-only token estimator against a temporary config. No provider calls are
 * made and no run directory is reserved.
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

	try {
		const estimate = await estimateAssertAiRun(normalized, request.signal);
		return json({ estimate, warnings: normalized.warnings });
	} catch (err) {
		if (request.signal.aborted) {
			return new Response(null, { status: 499 });
		}
		const message = err instanceof EstimateError ? err.message : (err as Error).message ?? String(err);
		return json(
			{ error: 'Token estimate unavailable.', details: [message] },
			{ status: 500 }
		);
	}
};
