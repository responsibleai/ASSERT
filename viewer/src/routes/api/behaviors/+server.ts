import { json } from '@sveltejs/kit';
import { listSuites, loadPolicy } from '$lib/server/data.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async () => {
	const suites = listSuites();
	const seen = new Map<string, { name: string; definition: string; suiteId: string }>();

	for (const suite of suites) {
		const taxonomy = loadPolicy(suite.suite_id);
		const behavior = taxonomy?.behavior;
		if (!behavior?.name) continue;
		if (seen.has(behavior.name)) continue;
		seen.set(behavior.name, {
			name: behavior.name,
			definition: behavior.definition ?? '',
			suiteId: suite.suite_id
		});
	}

	return json([...seen.values()]);
};
