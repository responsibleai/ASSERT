import { json } from '@sveltejs/kit';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { loadJudgedSamples } from '$lib/server/data.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params, url }) => {
	if (!isSafeArtifactId(params.suite) || !isSafeArtifactId(params.run)) {
		return json({ error: 'Invalid parameters' }, { status: 400 });
	}
	const behavior = url.searchParams.get('behavior');
	let samples = loadJudgedSamples(params.suite, params.run);
	if (behavior) samples = samples.filter((sample) => sample.behavior === behavior);
	return json(samples);
};
