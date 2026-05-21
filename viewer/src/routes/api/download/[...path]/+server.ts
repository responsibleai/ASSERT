import { error } from '@sveltejs/kit';
import { resolveArtifactPath } from '$lib/server/artifacts.js';
import path from 'node:path';
import fs from 'node:fs';
import { Readable } from 'node:stream';
import type { RequestHandler } from './$types.js';

const ALLOWED_FILES = new Set([
	'suite.json',
	'taxonomy.json',
	'test_set.jsonl',
	'manifest.json',
	'config.yaml',
	'scores.jsonl',
	'inference_set.jsonl',
	'metrics.json'
]);

export const GET: RequestHandler = async ({ params }) => {
	const reqPath = params.path;
	if (!reqPath) throw error(400, 'Missing path');

	let resolved: string;
	try {
		resolved = resolveArtifactPath(reqPath);
	} catch {
		throw error(403, 'Forbidden');
	}

	const basename = path.basename(resolved);
	if (!ALLOWED_FILES.has(basename)) {
		throw error(403, `File type not allowed: ${basename}`);
	}

	if (!fs.existsSync(resolved)) {
		throw error(404, 'File not found');
	}

	const stat = fs.statSync(resolved);
	const stream = Readable.toWeb(fs.createReadStream(resolved)) as ReadableStream<Uint8Array>;

	return new Response(stream, {
		headers: {
			'Content-Type': 'application/octet-stream',
			'Content-Disposition': `attachment; filename="${basename}"`,
			'Content-Length': String(stat.size)
		}
	});
};
