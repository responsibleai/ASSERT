import { json } from '@sveltejs/kit';
import { listSuites, loadPolicy } from '$lib/server/data.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async () => {
	const suites = listSuites();
	const seen = new Map<string, { name: string; definition: string; suiteId: string }>();

	for (const suite of suites) {
		const taxonomy = loadPolicy(suite.suite_id);
		if (!taxonomy?.behavior_categories) continue;
		for (const b of taxonomy.behavior_categories) {
			if (b.name && !seen.has(b.name)) {
				seen.set(b.name, {
					name: b.name,
					definition: b.definition ?? '',
					suiteId: suite.suite_id
				});
			}
		}
	}

	return json([...seen.values()]);
};
