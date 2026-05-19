import { json } from '@sveltejs/kit';
import { listSuites } from '$lib/server/data.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async () => {
	const suites = listSuites().map((s) => ({
		suite_id: s.suite_id,
		risk_name: s.concept_name,
		behavior_count: s.behavior_count
	}));
	return json(suites);
};
