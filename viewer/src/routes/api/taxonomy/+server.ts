import { json } from '@sveltejs/kit';
import fs from 'node:fs';
import path from 'node:path';
import { ARTIFACTS_ROOT } from '$lib/server/config.js';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types.js';

function hasNamedBehaviors(value: unknown): value is { name: string }[] {
	return Array.isArray(value)
		&& value.every(
			(sr) => sr !== null && typeof sr === 'object' && typeof (sr as { name?: unknown }).name === 'string'
		);
}

export const PUT: RequestHandler = async ({ request }) => {
	if (env.VIEWER_EDIT_MODE !== '1') {
		return json({ error: 'Editing is disabled' }, { status: 403 });
	}

	const { suite_id, taxonomy } = await request.json();

	if (typeof suite_id !== 'string' || !suite_id || !taxonomy) {
		return json({ error: 'suite_id and taxonomy are required' }, { status: 400 });
	}

	if (!taxonomy.spec || !hasNamedBehaviors(taxonomy.failure_modes)) {
		return json({ error: 'taxonomy must have spec and failure_modes' }, { status: 400 });
	}

	if (!isSafeArtifactId(suite_id)) {
		return json({ error: 'invalid suite_id' }, { status: 400 });
	}

	const suiteDir = path.join(ARTIFACTS_ROOT, suite_id);
	const taxonomyPath = path.join(suiteDir, 'taxonomy.json');

	if (!fs.existsSync(suiteDir)) {
		return json({ error: `Suite "${suite_id}" not found` }, { status: 404 });
	}

	// Only allow editing existing policies — reject if taxonomy.json is missing or malformed
	if (!fs.existsSync(taxonomyPath)) {
		return json({ error: 'No existing taxonomy to edit' }, { status: 400 });
	}

	let existing: { failure_modes?: unknown };
	try {
		existing = JSON.parse(fs.readFileSync(taxonomyPath, 'utf-8')) as { failure_modes?: unknown };
	} catch {
		return json({ error: 'Existing taxonomy is malformed' }, { status: 400 });
	}
	if (!hasNamedBehaviors(existing.failure_modes)) {
		return json({ error: 'Existing taxonomy is malformed' }, { status: 400 });
	}

	// Reject additions or deletions of failure_modes (only edits to existing nodes allowed)
	const existingBehaviors = existing.failure_modes;
	const incomingBehaviors = taxonomy.failure_modes as { name: string }[];
	const existingNames = existingBehaviors.map((sr) => sr.name);
	const incomingNames = incomingBehaviors.map((sr) => sr.name);
	if (existingNames.length !== incomingNames.length ||
		!existingNames.every((n: string, i: number) => n === incomingNames[i])) {
		return json({ error: 'Adding, removing, or reordering taxonomy nodes is not allowed' }, { status: 400 });
	}

	// Write taxonomy
	fs.writeFileSync(taxonomyPath, JSON.stringify(taxonomy, null, 2), 'utf-8');

	return json({ ok: true });
};
