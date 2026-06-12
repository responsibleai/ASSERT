// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { getModelCatalog } from '$lib/server/models.js';
import type { RequestHandler } from './$types.js';

/**
 * GET /api/models
 *
 * Returns the configured model catalog derived from server-side environment
 * variables (see lib/server/models.ts for the full list of keys consulted).
 * Used by the "new evaluation" wizard to populate model-selection dropdowns.
 */
export const GET: RequestHandler = async () => {
	const catalog = getModelCatalog(env);
	return json({
		models: catalog.models.map(({ id }) => ({ id })),
		defaultModel: catalog.defaultModel
	});
};
