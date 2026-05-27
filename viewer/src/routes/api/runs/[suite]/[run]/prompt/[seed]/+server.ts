// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { loadPromptDrawerItem } from '$lib/server/data.js';
import { isSafeArtifactId } from '$lib/server/artifacts.js';
import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params }) => {
	if (!isSafeArtifactId(params.suite) || !isSafeArtifactId(params.run)) {
		return json({ error: 'Invalid parameters' }, { status: 400 });
	}
	const item = await loadPromptDrawerItem(params.suite, params.run, params.seed);
	if (!item) {
		return json({ error: 'Prompt not found' }, { status: 404 });
	}
	return json(item);
};
