// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { json } from '@sveltejs/kit';
import fs from 'node:fs';
import path from 'node:path';
import { ARTIFACTS_ROOT } from '$lib/server/config.js';
import { isSafeArtifactId, SUITE_TEST_SET_FILE } from '$lib/server/artifacts.js';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types.js';

interface SeedRow {
	test_case_id?: unknown;
	seed?: { title?: unknown; description?: unknown } & Record<string, unknown>;
	[key: string]: unknown;
}

// Edit a single test-set seed (prompt or scenario) in place. Only the seed's
// title and description text are mutated; every other field on the row — and
// every other row in the file — is preserved exactly. Adding or removing seeds
// is not supported here.
export const PUT: RequestHandler = async ({ request }) => {
	if (env.VIEWER_EDIT_MODE !== '1') {
		return json({ error: 'Editing is disabled' }, { status: 403 });
	}

	const { suite_id, test_case_id, title, description } = await request.json();

	if (typeof suite_id !== 'string' || !suite_id) {
		return json({ error: 'suite_id is required' }, { status: 400 });
	}
	if (typeof test_case_id !== 'string' || !test_case_id) {
		return json({ error: 'test_case_id is required' }, { status: 400 });
	}
	if (typeof title !== 'string' || typeof description !== 'string') {
		return json({ error: 'title and description must be strings' }, { status: 400 });
	}
	if (!isSafeArtifactId(suite_id)) {
		return json({ error: 'invalid suite_id' }, { status: 400 });
	}

	const suiteDir = path.join(ARTIFACTS_ROOT, suite_id);
	const testSetPath = path.join(suiteDir, SUITE_TEST_SET_FILE);

	if (!fs.existsSync(testSetPath)) {
		return json({ error: 'No test set to edit' }, { status: 404 });
	}

	const raw = fs.readFileSync(testSetPath, 'utf-8');
	const lines = raw.split('\n');
	let matched = false;

	const updated = lines.map((line) => {
		if (line.trim() === '') return line;
		let row: SeedRow;
		try {
			row = JSON.parse(line) as SeedRow;
		} catch {
			// Preserve malformed/non-JSON lines untouched.
			return line;
		}
		if (row.test_case_id !== test_case_id) return line;
		matched = true;
		const seed = (row.seed && typeof row.seed === 'object' ? row.seed : {}) as Record<string, unknown>;
		row.seed = { ...seed, title, description };
		return JSON.stringify(row);
	});

	if (!matched) {
		return json({ error: `Seed "${test_case_id}" not found` }, { status: 404 });
	}

	fs.writeFileSync(testSetPath, updated.join('\n'), 'utf-8');

	return json({ ok: true });
};
