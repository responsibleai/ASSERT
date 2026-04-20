import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { error } from '@sveltejs/kit';
import type { PageServerLoad } from './$types.js';

export const load: PageServerLoad = async ({ params }) => {
	if (!isSafeArtifactId(params.suite_id)) throw error(400, 'Invalid suite ID');
	if (!isSafeArtifactId(params.run_id)) throw error(400, 'Invalid run ID');
	return {
		suite_id: params.suite_id,
		run_id: params.run_id
	};
};
