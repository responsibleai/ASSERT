// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/**
 * GET /api/suites/[suite_id]/config
 *
 * Returns a small, wizard-friendly summary of an existing suite's authored
 * configuration, read from its most recent run's `eval_config.yaml` or
 * completed-run `config.yaml`. The New
 * evaluation wizard uses this to pre-fill the "existing suite" flow (behavior,
 * application context, system prompt, target kind) when launched from a suite's
 * "Run evaluation" button via `/new?suite=<id>`.
 */

import { json } from '@sveltejs/kit';
import fs from 'node:fs';
import path from 'node:path';
import { parse as parseYaml } from 'yaml';
import { isSafeArtifactId, suiteDirPath, SUITE_TEST_SET_FILE } from '$lib/server/artifacts.js';
import type { RequestHandler } from './$types.js';

const RUN_CONFIG_FILES = ['eval_config.yaml', 'config.yaml'];
const MAX_TOOL_PREFILL_BYTES = 256 * 1024;

/** Count of test examples (non-empty rows) in the suite's test_set.jsonl. */
function countTestExamples(suiteDir: string): number {
	try {
		const raw = fs.readFileSync(path.join(suiteDir, SUITE_TEST_SET_FILE), 'utf-8');
		return raw.split('\n').filter((line) => line.trim().length > 0).length;
	} catch {
		return 0;
	}
}

/** Most recently modified run dir under `suiteDir` that has a persisted run config. */
function findLatestRunConfig(suiteDir: string): string | null {
	let entries: fs.Dirent[];
	try {
		entries = fs.readdirSync(suiteDir, { withFileTypes: true });
	} catch {
		return null;
	}
	let best: { path: string; mtimeMs: number } | null = null;
	for (const entry of entries) {
		if (!entry.isDirectory()) continue;
		if (entry.name === 'artifacts' || !isSafeArtifactId(entry.name)) continue;
		for (const configFile of RUN_CONFIG_FILES) {
			const configPath = path.join(suiteDir, entry.name, configFile);
			let stat: fs.Stats;
			try {
				stat = fs.statSync(configPath);
			} catch {
				continue;
			}
			if (!stat.isFile()) continue;
			if (!best || stat.mtimeMs > best.mtimeMs) {
				best = { path: configPath, mtimeMs: stat.mtimeMs };
			}
		}
	}
	return best ? best.path : null;
}

/** Run dir names under `suiteDir` (excludes the shared `artifacts` cache dir). */
function listRunIds(suiteDir: string): string[] {
	let entries: fs.Dirent[];
	try {
		entries = fs.readdirSync(suiteDir, { withFileTypes: true });
	} catch {
		return [];
	}
	return entries
		.filter((e) => e.isDirectory() && e.name !== 'artifacts' && isSafeArtifactId(e.name))
		.map((e) => e.name);
}

/**
 * Next free `v<N>` run id given the existing run ids. Re-running an existing
 * suite should not collide with a prior run, so we auto-increment past the
 * highest existing `v`-numbered run (v1 -> v2). Suites with no v-style runs
 * start at v1.
 */
function nextRunId(existingRunIds: string[]): string {
	const vNums = existingRunIds
		.map((id) => /^v(\d+)$/.exec(id))
		.filter((m): m is RegExpExecArray => m !== null)
		.map((m) => Number(m[1]));
	return vNums.length > 0 ? `v${Math.max(...vNums) + 1}` : 'v1';
}

function asString(value: unknown): string {
	return typeof value === 'string' ? value : '';
}

function asRecord(value: unknown): Record<string, unknown> {
	return value && typeof value === 'object' ? (value as Record<string, unknown>) : {};
}

function targetSummary(target: Record<string, unknown>): string {
	if (typeof target.callable === 'string') return `Callable target: ${target.callable}`;
	if (typeof target.agent === 'string') return `Agent target: ${target.agent}`;
	const tools = asRecord(target.tools);
	if (tools.module) return `Prompt Agent target with module tools: ${asString(tools.module)}`;
	if (tools.simulator && !tools.toolset) return `Prompt Agent target with generated tools`;
	if (target.model) return target.tools ? 'Prompt Agent target' : 'Hosted model target';
	return 'Existing target';
}

function modelName(value: unknown): string {
	if (typeof value === 'string') return value;
	return asString(asRecord(value).name);
}

function safeToolsetRef(value: unknown): string {
	if (typeof value !== 'string' || !value.trim()) return '';
	const ref = value.trim();
	if (path.isAbsolute(ref)) return '';
	const normalized = ref.replace(/\\/g, '/');
	const parts = normalized.split('/');
	if (parts.some((part) => part === '' || part === '..')) return '';
	const ext = path.extname(normalized).toLowerCase();
	return ext === '.yaml' || ext === '.yml' ? normalized : '';
}

function readToolsetYaml(ref: string, configPath: string, suiteDir: string): string {
	const candidates = [
		path.resolve(path.dirname(configPath), ref),
		path.resolve(suiteDir, ref),
		path.resolve(process.cwd(), '..', ref),
		path.resolve(process.cwd(), ref)
	];
	for (const candidate of candidates) {
		let stat: fs.Stats;
		try {
			stat = fs.statSync(candidate);
		} catch {
			continue;
		}
		if (!stat.isFile() || stat.size > MAX_TOOL_PREFILL_BYTES) continue;
		return fs.readFileSync(candidate, 'utf-8');
	}
	return '';
}

export const GET: RequestHandler = async ({ params }) => {
	const suiteId = params.suite_id ?? '';
	if (!suiteId || !isSafeArtifactId(suiteId)) {
		return json({ error: 'A valid suite id is required.' }, { status: 400 });
	}

	const suiteDir = suiteDirPath(suiteId);
	if (!fs.existsSync(suiteDir)) {
		return json({ error: `Suite "${suiteId}" was not found.` }, { status: 404 });
	}

	const existingRunIds = listRunIds(suiteDir);
	const suggestedRunId = nextRunId(existingRunIds);
	const testExampleCount = countTestExamples(suiteDir);

	const empty = {
		suiteId,
		behaviorName: null as string | null,
		context: '',
		systemPrompt: '',
		evaluationTarget: 'model' as 'model' | 'agent',
		existingRunIds,
		nextRunId: suggestedRunId,
		testExampleCount
	};

	const configPath = findLatestRunConfig(suiteDir);
	if (!configPath) {
		return json(empty);
	}

	let config: Record<string, unknown>;
	try {
		const parsed = parseYaml(fs.readFileSync(configPath, 'utf-8'));
		config = asRecord(parsed);
	} catch {
		return json(empty);
	}

	const behaviorName = asString(asRecord(config.behavior).name) || null;
	const context = asString(config.context);
	const pipeline = asRecord(config.pipeline);
	const target = asRecord(asRecord(pipeline.inference).target);
	const testSet = asRecord(pipeline.test_set);
	const systemPrompt = asString(target.system_prompt);
	const tools = asRecord(target.tools);
	const toolsMode =
		tools.toolset ? 'simulated' : tools.module ? 'real' : tools.simulator ? 'generated' : null;
	const simulatorModel = modelName(tools.simulator);
	const toolsetRef = safeToolsetRef(tools.toolset);
	const toolsetYaml = toolsetRef ? readToolsetYaml(toolsetRef, configPath, suiteDir) : '';
	const reuseExistingTarget = Boolean(target.callable || target.agent || tools.module);
	// Prompt Agent configs are encoded as target.model + target.tools. Treat that
	// as an agent rerun so the wizard does not silently drop tool configuration.
	const evaluationTarget: 'model' | 'agent' =
		target.tools || target.agent || target.callable ? 'agent' : 'model';

	return json({
		suiteId,
		behaviorName,
		context,
		systemPrompt,
		evaluationTarget,
		toolsMode,
		simulatorModel,
		toolsetYaml,
		toolsetFileName: toolsetRef ? path.basename(toolsetRef) : '',
		toolSource: asString(testSet.tool_source),
		reuseExistingTarget,
		targetSummary: targetSummary(target),
		existingRunIds,
		nextRunId: suggestedRunId,
		testExampleCount
	});
};
