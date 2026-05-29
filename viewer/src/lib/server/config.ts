// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export const ARTIFACTS_ROOT =
	process.env.ARTIFACTS_ROOT ?? path.resolve(__dirname, '..', '..', '..', '..', 'artifacts', 'results');

export const MEASUREMENTS_ROOT =
	process.env.MEASUREMENTS_ROOT ?? path.resolve(__dirname, '..', '..', '..', '..');
