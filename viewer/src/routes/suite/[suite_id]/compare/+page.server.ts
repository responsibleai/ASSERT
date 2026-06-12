// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { loadComparePageData } from '$lib/server/data.js';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { error } from '@sveltejs/kit';
import type { JudgedSample } from '$lib/types.js';
import type { PageServerLoad } from './$types.js';

export const load: PageServerLoad = async ({ params, url }) => {
	const { suite_id } = params;
	if (!isSafeArtifactId(suite_id)) throw error(400, 'Invalid suite ID');

	const runsParam = url.searchParams.get('runs');

	if (!runsParam) throw error(400, 'Missing "runs" query parameter');

	const runIds = runsParam.split(',').filter(Boolean);
	if (runIds.length < 2) throw error(400, 'Need at least 2 runs to compare');
	if (runIds.length > 4) throw error(400, 'Maximum 4 runs to compare');

	for (const runId of runIds) {
		if (!isSafeArtifactId(runId)) throw error(400, `Invalid run ID: ${runId}`);
	}

	const kind: 'prompts' | 'scenarios' =
		url.searchParams.get('kind') === 'scenarios' ? 'scenarios' : 'prompts';

	const payload = loadComparePageData(suite_id, runIds, kind);
	if (payload) return payload;

	// Scenarios-mode empty state: when one or more runs have no scenario
	// samples, we still want to render the toggle so the user can switch back
	// to Prompts. Reuse the prompts payload for the shared metadata (runs,
	// taxonomy, dimensionDefs) and blank out the comparison data so the page
	// can show a "No scenarios in these runs" message.
	if (kind === 'scenarios') {
		const promptsPayload = loadComparePageData(suite_id, runIds, 'prompts');
		if (!promptsPayload) throw error(404, 'One or more runs had no judged samples');
		return {
			...promptsPayload,
			kind: 'scenarios' as const,
			emptyKind: true,
			comparisons: [] as typeof promptsPayload.comparisons,
			samplesByBehavior: {} as Record<string, Record<string, JudgedSample[]>>,
			allMetrics: [] as string[]
		};
	}

	throw error(404, 'One or more runs had no judged samples');
};
