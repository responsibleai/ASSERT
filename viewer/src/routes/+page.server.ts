// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { listSuites } from '$lib/server/data.js';
import type { PageServerLoad } from './$types.js';

export const load: PageServerLoad = async () => {
	return { suites: listSuites() };
};
