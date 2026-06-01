// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { json } from '@sveltejs/kit';
import fs from 'node:fs';
import path from 'node:path';
import { ARTIFACTS_ROOT } from '$lib/server/config.js';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types.js';

export const PUT: RequestHandler = async ({ request }) => {
	if (env.VIEWER_EDIT_MODE !== '1') {
		return json({ error: 'Editing is disabled' }, { status: 403 });
	}

	const { suite_id, systematization } = await request.json();

	if (typeof suite_id !== 'string' || !suite_id) {
		return json({ error: 'suite_id is required' }, { status: 400 });
	}

	if (typeof systematization !== 'string') {
		return json({ error: 'systematization must be a string' }, { status: 400 });
	}

	if (!isSafeArtifactId(suite_id)) {
		return json({ error: 'invalid suite_id' }, { status: 400 });
	}

	const suiteDir = path.join(ARTIFACTS_ROOT, suite_id);
	const systematizationPath = path.join(suiteDir, 'systematization.json');

	if (!fs.existsSync(suiteDir)) {
		return json({ error: `Suite "${suite_id}" not found` }, { status: 404 });
	}

	// Only allow editing an existing systematization artifact.
	if (!fs.existsSync(systematizationPath)) {
		return json({ error: 'No existing systematization to edit' }, { status: 400 });
	}

	let existing: Record<string, unknown>;
	try {
		const parsed = JSON.parse(fs.readFileSync(systematizationPath, 'utf-8'));
		if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
			throw new Error('not an object');
		}
		existing = parsed as Record<string, unknown>;
	} catch {
		return json({ error: 'Existing systematization is malformed' }, { status: 400 });
	}

	// Mutate only the free-text field; preserve summary_items, meta, and any
	// other structured fields exactly.
	existing.systematization = systematization;

	fs.writeFileSync(systematizationPath, JSON.stringify(existing, null, 2), 'utf-8');

	return json({ ok: true });
};
