// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { parse as parseYaml } from 'yaml';
import { ARTIFACTS_ROOT } from './config.js';
import type { Manifest, Taxonomy, Suite } from '$lib/types.js';

export const SUITE_TEST_SET_FILE = 'test_set.jsonl';
export const RUN_INFERENCE_SET_FILE = 'inference_set.jsonl';
export const RUN_SCORES_FILE = 'scores.jsonl';
export const RUN_CONFIG_FILE = 'config.yaml';
export const RUN_MANIFEST_FILE = 'manifest.json';
export const VIEWER_CACHE_DIR = '.viewer';
export const VIEWER_RUN_MANIFEST_FILE = 'viewer_run_manifest.json';
export const VIEWER_PROMPT_ROWS_FILE = 'viewer_prompt_rows.json';
export const VIEWER_AUDIT_ROWS_FILE = 'viewer_audit_rows.json';
export const VIEWER_TRANSCRIPT_INDEX_FILE = 'viewer_transcript_index.json';
export const VIEWER_SCORE_INDEX_FILE = 'viewer_score_index.json';
export const SUITE_METADATA_FILE = 'suite.json';
export const SUITE_POLICY_FILE = 'taxonomy.json';
export const SUITE_SYSTEMATIZATION_FILE = 'systematization.json';
export const SUITE_ARTIFACTS_DIR = 'artifacts';
export const VIEWER_READ_MODEL_SCHEMA_VERSION = 4;
export const VIEWER_READ_MODEL_GENERATOR_VERSION = 'viewer-read-model-v3';

const SAFE_ID_RE = /^[a-z0-9][a-z0-9._-]*$/i;

export type UnifiedSeedRow = Record<string, unknown> & {
	kind?: unknown;
	test_case_id?: unknown;
	seed?: unknown;
	dimensions?: unknown;
};

export type UnifiedTranscriptRow = Record<string, unknown> & {
	kind?: unknown;
	test_case_id?: unknown;
	events?: unknown;
	llm_calls?: unknown;
	stop_reason?: unknown;
	behavior?: unknown;
	permissible?: unknown;
	target?: unknown;
	tester_model?: unknown;
	dimensions?: unknown;
};

export type UnifiedScoreRow = Record<string, unknown> & {
	kind?: unknown;
	test_case_id?: unknown;
	verdict?: unknown;
	judge_status?: unknown;
	judge_error?: unknown;
	target?: unknown;
	tester_model?: unknown;
	dimensions?: unknown;
};

export interface SuiteSnapshot {
	suiteId: string;
	suiteDir: string;
	suite: Suite | null;
	taxonomy: Taxonomy | null;
	seedRows: UnifiedSeedRow[];
	runIds: string[];
	systematization: Record<string, unknown> | null;
}

export interface RunSnapshot {
	suiteId: string;
	runId: string;
	runDir: string;
	manifest: Manifest | null;
	config: Record<string, unknown> | null;
	seedRows: UnifiedSeedRow[];
	scoreRows: UnifiedScoreRow[];
	transcriptRows: UnifiedTranscriptRow[];
	runtimeMode: string | null;
}

export interface ViewerReadModelFileMetadata {
	path: string;
	size_bytes: number;
	mtime_ms: number;
}

export interface ViewerRunManifestFile {
	schema_version: number;
	generator_version: string;
	suite_id: string;
	run_id: string;
	source_files: Record<string, ViewerReadModelFileMetadata>;
	derived_files: Record<string, ViewerReadModelFileMetadata>;
}

export interface ViewerIndexEntry {
	kind: 'prompt' | 'scenario';
	test_case_id: string;
	offset: number;
	length: number;
}

export interface ViewerIndexFile {
	items: Record<string, ViewerIndexEntry>;
}

export interface ViewerRunReadModel {
	manifest: ViewerRunManifestFile;
	promptRows: UnifiedScoreRow[];
	auditRows: UnifiedScoreRow[];
	transcriptIndex: ViewerIndexFile;
	scoreIndex: ViewerIndexFile;
}

export interface ViewerRunIndexes {
	manifest: ViewerRunManifestFile;
	transcriptIndex: ViewerIndexFile;
	scoreIndex: ViewerIndexFile;
}

interface JsonlReadOptions {
	missingOk?: boolean;
	lineMatcher?: (line: string) => boolean;
}

interface RunSnapshotOptions {
	includeTranscripts?: boolean;
	transcriptKind?: 'prompt' | 'scenario';
}

export class ArtifactParseError extends Error {
	filePath: string;
	format: 'json' | 'jsonl' | 'yaml';

	constructor(
		filePath: string,
		format: 'json' | 'jsonl' | 'yaml',
		message: string,
		options?: { cause?: unknown }
	) {
		super(message, options);
		this.name = 'ArtifactParseError';
		this.filePath = filePath;
		this.format = format;
	}
}

export class ViewerReadModelError extends Error {
	suiteId: string;
	runId: string;
	reason: 'missing' | 'stale' | 'invalid';

	constructor(
		suiteId: string,
		runId: string,
		reason: 'missing' | 'stale' | 'invalid',
		message: string
	) {
		super(message);
		this.name = 'ViewerReadModelError';
		this.suiteId = suiteId;
		this.runId = runId;
		this.reason = reason;
	}
}

function isMissingError(error: unknown): boolean {
	return Boolean(error && typeof error === 'object' && 'code' in error && error.code === 'ENOENT');
}

function readTextFile(filePath: string, { missingOk = false }: { missingOk?: boolean } = {}): string | null {
	try {
		return fs.readFileSync(filePath, 'utf-8');
	} catch (error) {
		if (missingOk && isMissingError(error)) return null;
		throw error;
	}
}

function viewerIndexKey(kind: 'prompt' | 'scenario', seedId: string): string {
	return `${kind}:${seedId}`;
}

function viewerArtifactPath(runDir: string, fileName: string): string {
	return path.join(runDir, VIEWER_CACHE_DIR, fileName);
}

function manifestRelativePath(baseDir: string, rawPath: string): string | null {
	const parts = rawPath.split(/[\\/]+/).filter((part) => part.length > 0 && part !== '.');
	if (parts.length === 0) {
		console.warn(
			`[viewer] refusing manifest path that normalizes to no segments: ${rawPath}`
		);
		return null;
	}
	if (parts.some((part) => part === '..')) {
		console.warn(
			`[viewer] refusing manifest path with parent-directory segments: ${rawPath}`
		);
		return null;
	}
	return path.join(baseDir, ...parts);
}

function manifestArtifactPath(suiteDir: string, rawPath: unknown): string | null {
	if (typeof rawPath !== 'string' || rawPath.length === 0) return null;
	if (path.isAbsolute(rawPath)) {
		// A tampered or corrupted manifest.json must not be able to redirect
		// viewer reads outside the suite directory via an absolute path,
		// which would otherwise bypass the relative-path '..' defense.
		console.warn(`[viewer] refusing absolute manifest artifact path: ${rawPath}`);
		return null;
	}
	return manifestRelativePath(suiteDir, rawPath);
}

function runSeedArtifactPath(suiteDir: string, manifest: Manifest | null): string {
	const seedArtifact = manifest?.artifact_versions?.test_set;
	const artifactPath = manifestArtifactPath(
		suiteDir,
		seedArtifact?.path ?? seedArtifact?.relative_path
	);
	if (artifactPath) return artifactPath;
	return path.join(suiteDir, SUITE_TEST_SET_FILE);
}

function runSeedRows(
	suiteDir: string,
	manifest: Manifest | null,
	seedRows: UnifiedSeedRow[] | undefined
): UnifiedSeedRow[] {
	const seedArtifact = manifest?.artifact_versions?.test_set;
	if (seedArtifact?.path || seedArtifact?.relative_path) {
		return readJsonlFile<UnifiedSeedRow>(runSeedArtifactPath(suiteDir, manifest), { missingOk: true });
	}
	return seedRows ?? readJsonlFile<UnifiedSeedRow>(path.join(suiteDir, SUITE_TEST_SET_FILE), { missingOk: true });
}

function rebuildViewerInstruction(runDir: string): string {
	const configPath = path.resolve(runDir, RUN_CONFIG_FILE);
	return `Rebuild it by re-running judge for this run: uv run assert-ai run --config ${configPath} --resume --force-stage judge`;
}

function validateViewerFileMetadata(
	suiteId: string,
	runId: string,
	runDir: string,
	recorded: ViewerReadModelFileMetadata
): void {
	const filePath = path.resolve(runDir, recorded.path);
	let sizeBytes: number;
	let mtimeMs: number;
	try {
		const stat = fs.statSync(filePath);
		sizeBytes = stat.size;
		mtimeMs = Math.trunc(stat.mtimeMs);
	} catch (error) {
		if (isMissingError(error)) {
			throw new ViewerReadModelError(
				suiteId,
				runId,
				'missing',
				`Viewer read model is missing required artifact "${recorded.path}" for ${suiteId}/${runId}. ${rebuildViewerInstruction(runDir)}`
			);
		}
		throw error;
	}

	if (sizeBytes !== recorded.size_bytes || mtimeMs !== recorded.mtime_ms) {
		throw new ViewerReadModelError(
			suiteId,
			runId,
			'stale',
			`Viewer read model is stale for ${suiteId}/${runId}. ${rebuildViewerInstruction(runDir)}`
		);
	}
}

export function readJsonFile<T>(
	filePath: string,
	{ missingOk = false }: { missingOk?: boolean } = {}
): T | null {
	const text = readTextFile(filePath, { missingOk });
	if (text === null) return null;
	try {
		return JSON.parse(text) as T;
	} catch (error) {
		throw new ArtifactParseError(filePath, 'json', `Invalid JSON in ${filePath}`, { cause: error });
	}
}

export function readJsonlFile<T>(
	filePath: string,
	{ missingOk = false, lineMatcher }: JsonlReadOptions = {}
): T[] {
	const text = readTextFile(filePath, { missingOk });
	if (text === null) return [];
	const trimmed = text.trim();
	if (!trimmed) return [];
	try {
		return trimmed.split('\n').flatMap((line, index) => {
			try {
				const row = JSON.parse(line) as T;
				if (lineMatcher && !lineMatcher(line)) return [];
				return [row];
			} catch (error) {
				throw new ArtifactParseError(
					filePath,
					'jsonl',
					`Invalid JSONL in ${filePath} on line ${index + 1}`,
					{ cause: error }
				);
			}
		});
	} catch (error) {
		if (error instanceof ArtifactParseError) throw error;
		throw new ArtifactParseError(filePath, 'jsonl', `Invalid JSONL in ${filePath}`, { cause: error });
	}
}

function readJsonRowByOffset<T>(filePath: string, offset: number, length: number): T {
	const fd = fs.openSync(filePath, 'r');
	try {
		const buffer = Buffer.alloc(length);
		const bytesRead = fs.readSync(fd, buffer, 0, length, offset);
		const text = buffer.subarray(0, bytesRead).toString('utf-8').trim();
		if (!text) {
			throw new ArtifactParseError(filePath, 'jsonl', `Missing JSONL row in ${filePath} at offset ${offset}`);
		}
		try {
			return JSON.parse(text) as T;
		} catch (error) {
			throw new ArtifactParseError(
				filePath,
				'jsonl',
				`Invalid JSONL row in ${filePath} at offset ${offset}`,
				{ cause: error }
			);
		}
	} finally {
		fs.closeSync(fd);
	}
}

export function readLiveInferenceJsonlFile<T>(
	filePath: string,
	{ missingOk = false, lineMatcher }: JsonlReadOptions = {}
): T[] {
	const text = readTextFile(filePath, { missingOk });
	if (text === null) return [];
	if (!text.trim()) return [];

	const hasTrailingNewline = text.endsWith('\n');
	const segments = text.split('\n');
	if (hasTrailingNewline && segments[segments.length - 1] === '') {
		segments.pop();
	}

	const rows: T[] = [];
	for (const [index, line] of segments.entries()) {
		const isFinalSegment = index === segments.length - 1;
		try {
			const row = JSON.parse(line) as T;
			if (lineMatcher && !lineMatcher(line)) continue;
			rows.push(row);
		} catch (error) {
			if (isFinalSegment && !hasTrailingNewline) {
				break;
			}
			throw new ArtifactParseError(
				filePath,
				'jsonl',
				`Invalid JSONL in ${filePath} on line ${index + 1}`,
				{ cause: error }
			);
		}
	}

	return rows;
}

function escapeForRegExp(value: string): string {
	return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function buildJsonStringFieldMatcher(fieldName: string, value: string): (line: string) => boolean {
	const pattern = new RegExp(`"${escapeForRegExp(fieldName)}"\\s*:\\s*"${escapeForRegExp(value)}"`);
	return (line: string) => pattern.test(line);
}

async function readJsonlMatchingRow<T>(
	filePath: string,
	{
		missingOk = false,
		lineMatcher
	}: JsonlReadOptions & { lineMatcher: (line: string) => boolean }
): Promise<T | null> {
	try {
		const stream = fs.createReadStream(filePath, { encoding: 'utf-8' });
		const lines = readline.createInterface({
			input: stream,
			crlfDelay: Infinity
		});

		try {
			let matchedRow: T | null = null;
			for await (const line of lines) {
				try {
					const row = JSON.parse(line) as T;
					if (!lineMatcher(line)) continue;
					if (matchedRow !== null) {
						throw new ArtifactParseError(
							filePath,
							'jsonl',
							`Duplicate matching JSONL rows in ${filePath}`
						);
					}
					matchedRow = row;
				} catch (error) {
					if (error instanceof ArtifactParseError) throw error;
					throw new ArtifactParseError(
						filePath,
						'jsonl',
						`Invalid JSONL in ${filePath} while reading matching row`,
						{ cause: error }
					);
				}
			}
			return matchedRow;
		} finally {
			lines.close();
			stream.destroy();
		}
	} catch (error) {
		if (missingOk && isMissingError(error)) return null;
		throw error;
	}
}

function readLiveJsonlMatchingRow<T>(
	filePath: string,
	{
		missingOk = false,
		lineMatcher
	}: JsonlReadOptions & { lineMatcher: (line: string) => boolean }
): T | null {
	const text = readTextFile(filePath, { missingOk });
	if (text === null) return null;
	if (!text.trim()) return null;

	const hasTrailingNewline = text.endsWith('\n');
	const segments = text.split('\n');
	if (hasTrailingNewline && segments[segments.length - 1] === '') {
		segments.pop();
	}

	let matchedRow: T | null = null;
	for (const [index, line] of segments.entries()) {
		const isFinalSegment = index === segments.length - 1;
		try {
			const row = JSON.parse(line) as T;
			if (!lineMatcher(line)) continue;
			if (matchedRow !== null) {
				throw new ArtifactParseError(filePath, 'jsonl', `Duplicate matching JSONL rows in ${filePath}`);
			}
			matchedRow = row;
		} catch (error) {
			if (error instanceof ArtifactParseError) throw error;
			if (isFinalSegment && !hasTrailingNewline) {
				break;
			}
			throw new ArtifactParseError(
				filePath,
				'jsonl',
				`Invalid JSONL in ${filePath} while reading matching row`,
				{ cause: error }
			);
		}
	}

	return matchedRow;
}

export function readYamlFile<T>(
	filePath: string,
	{ missingOk = false }: { missingOk?: boolean } = {}
): T | null {
	const text = readTextFile(filePath, { missingOk });
	if (text === null) return null;
	try {
		return parseYaml(text) as T;
	} catch (error) {
		throw new ArtifactParseError(filePath, 'yaml', `Invalid YAML in ${filePath}`, { cause: error });
	}
}

export function listSubdirectories(dirPath: string): string[] {
	try {
		return fs
			.readdirSync(dirPath, { withFileTypes: true })
			.filter((entry) => entry.isDirectory())
			.map((entry) => entry.name);
	} catch (error) {
		if (isMissingError(error)) return [];
		throw error;
	}
}

function listRunIds(suiteDir: string): string[] {
	return listSubdirectories(suiteDir).filter((entry) => {
		if (entry === SUITE_ARTIFACTS_DIR) return false;
		if (!isSafeArtifactId(entry)) return false;
		const runDir = path.join(suiteDir, entry);
		return (
			fs.existsSync(path.join(runDir, RUN_MANIFEST_FILE)) ||
			fs.existsSync(path.join(runDir, RUN_CONFIG_FILE)) ||
			fs.existsSync(path.join(runDir, RUN_INFERENCE_SET_FILE)) ||
			fs.existsSync(path.join(runDir, RUN_SCORES_FILE))
		);
	});
}

export function isSafeArtifactId(id: string): boolean {
	return id.length > 0 && id.length <= 255 && SAFE_ID_RE.test(id) && !id.includes('..');
}

export function requireSafeId(id: string, label: string): void {
	if (!isSafeArtifactId(id)) {
		throw new Error(`Invalid ${label}: ${id}`);
	}
}

export function suiteDirPath(suiteId: string): string {
	requireSafeId(suiteId, 'suite ID');
	return path.join(ARTIFACTS_ROOT, suiteId);
}

export function runDirPath(suiteId: string, runId: string): string {
	requireSafeId(runId, 'run ID');
	return path.join(suiteDirPath(suiteId), runId);
}

export function resolveArtifactPath(requestPath: string): string {
	const artifactsRoot = path.resolve(ARTIFACTS_ROOT);
	const resolvedPath = path.resolve(artifactsRoot, requestPath);
	const relativePath = path.relative(artifactsRoot, resolvedPath);
	if (relativePath.startsWith('..') || path.isAbsolute(relativePath)) {
		throw new Error('Artifact path escaped artifacts root');
	}
	return resolvedPath;
}

function readObject(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: null;
}

export function loadRunJudgeTaxonomy(config: Record<string, unknown> | null): Taxonomy | null {
	return loadRunJudgeTaxonomyFromArtifacts(config, null, null);
}

export function loadRunJudgeTaxonomyFromArtifacts(
	config: Record<string, unknown> | null,
	artifacts: Record<string, unknown> | null,
	runDir: string | null = null
): Taxonomy | null {
	const suiteDir = suiteDirPathFromConfig(config);
	const systematize = readObject(artifacts?.systematize);
	const artifactTaxonomyPath = typeof systematize?.path === 'string' ? systematize.path : null;
	if (artifactTaxonomyPath) {
		const resolvedArtifactPath = manifestArtifactPath(suiteDir, artifactTaxonomyPath);
		const artifactTaxonomy = resolvedArtifactPath
			? readJsonFile<Taxonomy>(resolvedArtifactPath, { missingOk: true })
			: null;
		if (artifactTaxonomy) return artifactTaxonomy;
	}

	const pipeline = readObject(config?.pipeline);
	const judge = readObject(pipeline?.judge);
	const rawTaxonomyPath = typeof judge?.taxonomy_path === 'string' ? judge.taxonomy_path : null;
	if (!rawTaxonomyPath) return null;
	return loadTaxonomyPath(rawTaxonomyPath, runDir ?? suiteDir);
}

function loadTaxonomyPath(rawTaxonomyPath: string, baseDir: string): Taxonomy | null {
	if (path.isAbsolute(rawTaxonomyPath)) return null;
	const resolved = path.resolve(baseDir, rawTaxonomyPath);
	const artifactsRoot = path.resolve(ARTIFACTS_ROOT);
	const relativeToArtifacts = path.relative(artifactsRoot, resolved);
	if (relativeToArtifacts.startsWith('..') || path.isAbsolute(relativeToArtifacts)) return null;
	return readJsonFile<Taxonomy>(resolved, { missingOk: true });
}

function suiteDirPathFromConfig(config: Record<string, unknown> | null): string {
	const suite = typeof config?.suite === 'string' ? config.suite : null;
	return suite ? suiteDirPath(suite) : ARTIFACTS_ROOT;
}

export function loadRunJudgeTaxonomyForRun(suiteId: string, runId: string): Taxonomy | null {
	const runDir = runDirPath(suiteId, runId);
	const config = readYamlFile<Record<string, unknown>>(path.join(runDir, RUN_CONFIG_FILE), {
		missingOk: true
	});
	const manifest = readJsonFile<Manifest>(path.join(runDir, RUN_MANIFEST_FILE), { missingOk: true });
	return loadRunJudgeTaxonomyFromArtifacts(config, manifest?.artifact_versions ?? null, runDir);
}

export function loadRunRuntimeMode(config: Record<string, unknown> | null): string | null {
	const pipeline = readObject(config?.pipeline);
	const inference = readObject(pipeline?.inference);
	const target = readObject(inference?.target);
	const tools = readObject(target?.tools);

	if (typeof target?.connector === 'string' && target.connector) return 'external';
	if (typeof tools?.module === 'string' && tools.module) return 'tool_module';
	if (typeof tools?.toolset === 'string' && tools.toolset) return 'simulated';

	const targetModel = readObject(target?.model);
	if (typeof targetModel?.name === 'string' && targetModel.name) return 'chat';
	return null;
}

export function loadViewerRunReadModel(suiteId: string, runId: string): ViewerRunReadModel {
	const { manifest, transcriptIndex, scoreIndex, runDir } = loadViewerRunIndexesWithRunDir(suiteId, runId);
	return {
		manifest,
		promptRows:
			readJsonFile<UnifiedScoreRow[]>(viewerArtifactPath(runDir, VIEWER_PROMPT_ROWS_FILE)) ?? [],
		auditRows:
			readJsonFile<UnifiedScoreRow[]>(viewerArtifactPath(runDir, VIEWER_AUDIT_ROWS_FILE)) ?? [],
		transcriptIndex,
		scoreIndex
	};
}

function loadViewerRunIndexesWithRunDir(suiteId: string, runId: string): ViewerRunIndexes & { runDir: string } {
	const runDir = runDirPath(suiteId, runId);
	const manifest = readJsonFile<ViewerRunManifestFile>(viewerArtifactPath(runDir, VIEWER_RUN_MANIFEST_FILE), {
		missingOk: true
	});
	if (!manifest) {
		throw new ViewerReadModelError(
			suiteId,
			runId,
			'missing',
			`Viewer read model is missing for ${suiteId}/${runId}. ${rebuildViewerInstruction(runDir)}`
		);
	}
	if (
		manifest.schema_version !== VIEWER_READ_MODEL_SCHEMA_VERSION ||
		manifest.generator_version !== VIEWER_READ_MODEL_GENERATOR_VERSION
	) {
		throw new ViewerReadModelError(
			suiteId,
			runId,
			'invalid',
			`Viewer read model is incompatible for ${suiteId}/${runId}. ${rebuildViewerInstruction(runDir)}`
		);
	}

	for (const fileMeta of Object.values(manifest.source_files ?? {})) {
		validateViewerFileMetadata(suiteId, runId, runDir, fileMeta);
	}
	for (const fileMeta of Object.values(manifest.derived_files ?? {})) {
		validateViewerFileMetadata(suiteId, runId, runDir, fileMeta);
	}

	return {
		manifest,
		transcriptIndex:
			readJsonFile<ViewerIndexFile>(viewerArtifactPath(runDir, VIEWER_TRANSCRIPT_INDEX_FILE)) ?? { items: {} },
		scoreIndex: readJsonFile<ViewerIndexFile>(viewerArtifactPath(runDir, VIEWER_SCORE_INDEX_FILE)) ?? { items: {} },
		runDir
	};
}

export function loadViewerRunIndexes(suiteId: string, runId: string): ViewerRunIndexes {
	const { manifest, transcriptIndex, scoreIndex } = loadViewerRunIndexesWithRunDir(suiteId, runId);
	return { manifest, transcriptIndex, scoreIndex };
}

export function loadSuiteSnapshot(suiteId: string): SuiteSnapshot | null {
	const suiteDir = suiteDirPath(suiteId);
	const suite = readJsonFile<Suite>(path.join(suiteDir, SUITE_METADATA_FILE), { missingOk: true });
	const taxonomy = readJsonFile<Taxonomy>(path.join(suiteDir, SUITE_POLICY_FILE), { missingOk: true });
	if (!suite && !taxonomy) return null;

	return {
		suiteId,
		suiteDir,
		suite,
		taxonomy,
		seedRows: readJsonlFile<UnifiedSeedRow>(path.join(suiteDir, SUITE_TEST_SET_FILE), { missingOk: true }),
		runIds: listRunIds(suiteDir),
		systematization: readJsonFile<Record<string, unknown>>(
			path.join(suiteDir, SUITE_SYSTEMATIZATION_FILE),
			{ missingOk: true }
		)
	};
}

export function loadRunSnapshot(
	suiteId: string,
	runId: string,
	seedRows?: UnifiedSeedRow[],
	options: RunSnapshotOptions = {}
): RunSnapshot {
	const runDir = runDirPath(suiteId, runId);
	const config = readYamlFile<Record<string, unknown>>(path.join(runDir, RUN_CONFIG_FILE), {
		missingOk: true
	});
	const manifest = readJsonFile<Manifest>(path.join(runDir, RUN_MANIFEST_FILE), { missingOk: true });
	const inferenceRunning = manifest?.stages?.inference === 'running';
	const includeTranscripts = options.includeTranscripts ?? true;
	const transcriptLineMatcher = options.transcriptKind
		? buildJsonStringFieldMatcher('type', options.transcriptKind)
		: undefined;

	return {
		suiteId,
		runId,
		runDir,
		manifest,
		config,
		seedRows: runSeedRows(suiteDirPath(suiteId), manifest, seedRows),
		scoreRows: readJsonlFile<UnifiedScoreRow>(path.join(runDir, RUN_SCORES_FILE), { missingOk: true }),
		transcriptRows: !includeTranscripts
			? []
			: inferenceRunning
				? readLiveInferenceJsonlFile<UnifiedTranscriptRow>(path.join(runDir, RUN_INFERENCE_SET_FILE), {
						missingOk: true,
						lineMatcher: transcriptLineMatcher
					})
				: readJsonlFile<UnifiedTranscriptRow>(path.join(runDir, RUN_INFERENCE_SET_FILE), {
						missingOk: true,
						lineMatcher: transcriptLineMatcher
					}),
		runtimeMode: loadRunRuntimeMode(config)
	};
}

export async function loadRunTranscriptRow(
	suiteId: string,
	runId: string,
	seedId: string,
	kind: 'prompt' | 'scenario'
): Promise<UnifiedTranscriptRow | null> {
	const seedMatcher = buildJsonStringFieldMatcher('test_case_id', seedId);
	const kindMatcher = buildJsonStringFieldMatcher('type', kind);
	const lineMatcher = (line: string) => seedMatcher(line) && kindMatcher(line);
	const runDir = runDirPath(suiteId, runId);
	const manifest = readJsonFile<Manifest>(path.join(runDir, RUN_MANIFEST_FILE), { missingOk: true });
	const inferenceRunning = manifest?.stages?.inference === 'running';

	return inferenceRunning
		? readLiveJsonlMatchingRow<UnifiedTranscriptRow>(path.join(runDir, RUN_INFERENCE_SET_FILE), {
				missingOk: true,
				lineMatcher
			})
		: readJsonlMatchingRow<UnifiedTranscriptRow>(path.join(runDir, RUN_INFERENCE_SET_FILE), {
				missingOk: true,
				lineMatcher
			});
}

export async function loadRunScoreRow(
	suiteId: string,
	runId: string,
	seedId: string,
	kind: 'prompt' | 'scenario'
): Promise<UnifiedScoreRow | null> {
	const seedMatcher = buildJsonStringFieldMatcher('test_case_id', seedId);
	const kindMatcher = buildJsonStringFieldMatcher('type', kind);
	const lineMatcher = (line: string) => seedMatcher(line) && kindMatcher(line);

	return readJsonlMatchingRow<UnifiedScoreRow>(
		path.join(runDirPath(suiteId, runId), RUN_SCORES_FILE),
		{ missingOk: true, lineMatcher }
	);
}

export function loadIndexedRunTranscriptRow(
	suiteId: string,
	runId: string,
	indexFile: ViewerIndexFile,
	seedId: string,
	kind: 'prompt' | 'scenario'
): UnifiedTranscriptRow | null {
	const entry = indexFile.items?.[viewerIndexKey(kind, seedId)];
	if (!entry) return null;
	return readJsonRowByOffset<UnifiedTranscriptRow>(
		path.join(runDirPath(suiteId, runId), RUN_INFERENCE_SET_FILE),
		entry.offset,
		entry.length
	);
}

export function loadIndexedRunScoreRow(
	suiteId: string,
	runId: string,
	indexFile: ViewerIndexFile,
	seedId: string,
	kind: 'prompt' | 'scenario'
): UnifiedScoreRow | null {
	const entry = indexFile.items?.[viewerIndexKey(kind, seedId)];
	if (!entry) return null;
	return readJsonRowByOffset<UnifiedScoreRow>(
		path.join(runDirPath(suiteId, runId), RUN_SCORES_FILE),
		entry.offset,
		entry.length
	);
}
