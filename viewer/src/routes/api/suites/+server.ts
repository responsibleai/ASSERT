import { json } from '@sveltejs/kit';
import { listSuites } from '$lib/server/data.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async () => {
	const suites = listSuites().map((s) => ({
		suite_id: s.suite_id,
		behavior_name: s.behavior_name,
		behavior_category_count: s.behavior_category_count
	}));
	return json(suites);
};
