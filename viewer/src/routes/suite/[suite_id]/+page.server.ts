import { loadSuitePageData } from '$lib/server/data.js';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types.js';

export const load: PageServerLoad = async ({ params }) => {
	if (!isSafeArtifactId(params.suite_id)) throw error(400, 'Invalid suite ID');
	const payload = loadSuitePageData(params.suite_id);
	if (!payload) throw error(404, `Suite "${params.suite_id}" not found`);
	return payload;
};
