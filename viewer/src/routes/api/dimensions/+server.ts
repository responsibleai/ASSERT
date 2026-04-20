import { json } from '@sveltejs/kit';
import { loadDimensions } from '$lib/server/dimensions.js';
import type { RequestHandler } from './$types.js';

export const GET: RequestHandler = async () => {
	return json(loadDimensions());
};
