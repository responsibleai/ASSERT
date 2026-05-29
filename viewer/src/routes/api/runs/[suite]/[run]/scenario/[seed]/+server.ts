// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { json } from '@sveltejs/kit';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { loadScenarioDrawerItem } from '$lib/server/data.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params }) => {
	if (!isSafeArtifactId(params.suite) || !isSafeArtifactId(params.run)) {
		return json({ error: 'Invalid parameters' }, { status: 400 });
	}
	const item = await loadScenarioDrawerItem(params.suite, params.run, params.seed);
	if (!item) {
		return json({ error: 'Scenario not found' }, { status: 404 });
	}
	return json(item);
};
