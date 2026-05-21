import { json } from '@sveltejs/kit';
import fs from 'node:fs';
import path from 'node:path';
import { ARTIFACTS_ROOT } from '$lib/server/config.js';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { env } from '$env/dynamic/private';
import type { RequestHandler } from './$types.js';

function hasNamedBehaviorCategories(value: unknown): value is { name: string }[] {
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

	if (!taxonomy.behavior || !hasNamedBehaviorCategories(taxonomy.behavior_categories)) {
		return json({ error: 'taxonomy must have behavior and behavior_categories' }, { status: 400 });
	}

	if (!isSafeArtifactId(suite_id)) {
		return json({ error: 'invalid suite_id' }, { status: 400 });
	}

	const suiteDir = path.join(ARTIFACTS_ROOT, suite_id);
	const policyPath = path.join(suiteDir, 'taxonomy.json');

	if (!fs.existsSync(suiteDir)) {
		return json({ error: `Suite "${suite_id}" not found` }, { status: 404 });
	}

	// Only allow editing existing policies — reject if taxonomy.json is missing or malformed
	if (!fs.existsSync(policyPath)) {
		return json({ error: 'No existing taxonomy to edit' }, { status: 400 });
	}

	let existing: { behavior_categories?: unknown };
	try {
		existing = JSON.parse(fs.readFileSync(policyPath, 'utf-8')) as { behavior_categories?: unknown };
	} catch {
		return json({ error: 'Existing taxonomy is malformed' }, { status: 400 });
	}
	if (!hasNamedBehaviorCategories(existing.behavior_categories)) {
		return json({ error: 'Existing taxonomy is malformed' }, { status: 400 });
	}

	// Reject additions or deletions of behavior_categories (only edits to existing nodes allowed)
	const existingBehaviorCategories = existing.behavior_categories;
	const incomingBehaviorCategories = taxonomy.behavior_categories as { name: string }[];
	const existingNames = existingBehaviorCategories.map((sr) => sr.name);
	const incomingNames = incomingBehaviorCategories.map((sr) => sr.name);
	if (existingNames.length !== incomingNames.length ||
		!existingNames.every((n: string, i: number) => n === incomingNames[i])) {
		return json({ error: 'Adding, removing, or reordering taxonomy nodes is not allowed' }, { status: 400 });
	}

	// Write taxonomy
	fs.writeFileSync(policyPath, JSON.stringify(taxonomy, null, 2), 'utf-8');

	return json({ ok: true });
};

