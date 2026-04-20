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

	const { suite_id, policy } = await request.json();

	if (typeof suite_id !== 'string' || !suite_id || !policy) {
		return json({ error: 'suite_id and policy are required' }, { status: 400 });
	}

	if (!policy.concept || !hasNamedBehaviors(policy.behaviors)) {
		return json({ error: 'policy must have concept and behaviors' }, { status: 400 });
	}

	if (!isSafeArtifactId(suite_id)) {
		return json({ error: 'invalid suite_id' }, { status: 400 });
	}

	const suiteDir = path.join(ARTIFACTS_ROOT, suite_id);
	const policyPath = path.join(suiteDir, 'policy.json');

	if (!fs.existsSync(suiteDir)) {
		return json({ error: `Suite "${suite_id}" not found` }, { status: 404 });
	}

	// Only allow editing existing policies — reject if policy.json is missing or malformed
	if (!fs.existsSync(policyPath)) {
		return json({ error: 'No existing policy to edit' }, { status: 400 });
	}

	let existing: { behaviors?: unknown };
	try {
		existing = JSON.parse(fs.readFileSync(policyPath, 'utf-8')) as { behaviors?: unknown };
	} catch {
		return json({ error: 'Existing policy is malformed' }, { status: 400 });
	}
	if (!hasNamedBehaviors(existing.behaviors)) {
		return json({ error: 'Existing policy is malformed' }, { status: 400 });
	}

	// Reject additions or deletions of behaviors (only edits to existing nodes allowed)
	const existingBehaviors = existing.behaviors;
	const incomingBehaviors = policy.behaviors as { name: string }[];
	const existingNames = existingBehaviors.map((sr) => sr.name);
	const incomingNames = incomingBehaviors.map((sr) => sr.name);
	if (existingNames.length !== incomingNames.length ||
		!existingNames.every((n: string, i: number) => n === incomingNames[i])) {
		return json({ error: 'Adding, removing, or reordering policy nodes is not allowed' }, { status: 400 });
	}

	// Write policy
	fs.writeFileSync(policyPath, JSON.stringify(policy, null, 2), 'utf-8');

	return json({ ok: true });
};
