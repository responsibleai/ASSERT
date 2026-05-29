// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { json } from '@sveltejs/kit';
import { loadRunStatusPayload } from '$lib/server/runner.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async ({ params }) => {
	const payload = loadRunStatusPayload(params.suite, params.run);
	if (!payload) {
		return json({ error: 'Run not found' }, { status: 404 });
	}
	return json(payload);
};
