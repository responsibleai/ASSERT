import { loadRunPageData } from '$lib/server/data.js';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types.js';

export const load: PageServerLoad = async ({ params, url }) => {
	if (!isSafeArtifactId(params.suite_id)) throw error(400, 'Invalid suite ID');
	if (!isSafeArtifactId(params.run_id)) throw error(400, 'Invalid run ID');
	const activeTab = url.searchParams.get('tab') === 'audit' ? 'audit' : 'prompts';
	const payload = loadRunPageData(params.suite_id, params.run_id, activeTab);
	if (!payload) throw error(404, `Run "${params.run_id}" not found in suite "${params.suite_id}"`);
	return payload;
};
