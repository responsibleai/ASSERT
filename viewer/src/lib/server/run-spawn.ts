// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/**
 * Translation + execution helpers for POST /api/runs.
 *
 * The wizard collects state under post-PR#23 names (`systematize`,
 * `testCasesPipeline`, `inferencePipeline`, `scenarioPipeline`, `dimensions`,
 * `tester`, …). This module is the boundary between that UI JSON payload and
 * the ASSERT YAML schema — it validates the payload, snake_cases keys for the
 * runner, and inlines the behavior description so the wizard can submit a
 * single self-contained config.
 *
 * Pipeline:
 *   normalizeWizardPayload(raw)
 *     -> validates the wizard JSON
 *     -> returns { suite, run, behaviorName, configObject, warnings }
 *
 *   writeRunConfigFiles(...)
 *     -> creates artifacts/results/<suite>/<run>/ atomically (mkdir is the lock)
 *     -> writes eval_config.yaml (single-YAML authoring; behavior description
 *        lives inline in behavior.description)
 *
 *   spawnAssertEvalRun(...)
 *     -> spawns `assert-eval run --config <eval_config.yaml>` detached
 *     -> waits for the OS spawn/error event before resolving so a missing
 *        binary surfaces as HTTP 500 (not 200 then a forever-pending monitor)
 *
 * All errors thrown here are typed so the +server.ts handler can map them to
 * specific HTTP status codes without inspecting `.message`.
 */

import { spawn, type ChildProcess } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { stringify as stringifyYaml } from 'yaml';
import {
	isSafeArtifactId,
	requireSafeId,
	runDirPath,
	suiteDirPath
} from './artifacts.js';
import { MEASUREMENTS_ROOT } from './config.js';

// ─── Errors ────────────────────────────────────────────────────────────

export class WizardValidationError extends Error {
	details: string[];
	constructor(details: string[]) {
		super(details.join('\n') || 'Invalid wizard payload');
		this.name = 'WizardValidationError';
		this.details = details;
	}
}

export class RunConflictError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'RunConflictError';
	}
}

export class SpawnError extends Error {
	cause?: unknown;
	constructor(message: string, cause?: unknown) {
		super(message);
		this.name = 'SpawnError';
		this.cause = cause;
	}
}

// ─── Wizard payload (mirrors the wizard's local state shape) ───────────
//
// All keys use post-PR#23 terminology (systematize / test_set / inference /
// scenario; dimensions; tester). The wizard's `scenarioPipeline.scenarioEvalConfig.tester`
// is what the YAML calls `pipeline.inference.tester`.

interface WizardBehaviorExisting {
	mode: 'existing';
	name?: string;
	definition?: string;
	suiteId?: string;
}
interface WizardBehaviorCreate {
	mode: 'create';
	name?: string;
	definition?: string;
}
type WizardBehavior = WizardBehaviorExisting | WizardBehaviorCreate;

interface WizardModelStub {
	model?: string;
	temperature?: number;
	maxTokens?: number;
}

interface WizardSystematizeConfig extends WizardModelStub {
	behaviorCategoryCount?: number;
	deepResearchAgent?: boolean;
}

interface WizardPromptTestCasesConfig extends WizardModelStub {
	budget?: number;
}

interface WizardPromptEvalConfig {
	targetModel?: string;
	judgeModel?: string;
}

interface WizardScenarioTestCasesConfig extends WizardModelStub {
	modality?: 'conversation' | 'agentic';
	budget?: number;
}

interface WizardScenarioStageModel extends WizardModelStub {
	judgePasses?: number;
}

interface WizardScenarioEvalConfig {
	target?: WizardScenarioStageModel;
	tester?: WizardScenarioStageModel;
	judge?: WizardScenarioStageModel;
	maxTurns?: number;
}

interface WizardJudgeDimension {
	name: string;
	description?: string;
	rubric?: string;
}

interface WizardEvalDimension {
	name: string;
	levels?: string[];
}

interface WizardPayload {
	behavior?: WizardBehavior;
	applicationContext?: string;
	evaluationTarget?: 'model' | 'agent';
	systemPrompt?: string;
	source?: 'new' | 'existing';
	existingSuiteId?: string;
	systematize?: { mode?: string; config?: WizardSystematizeConfig };
	testCasesPipeline?: {
		promptTestCases?: boolean;
		config?: WizardPromptTestCasesConfig;
		dimensionBased?: boolean;
		dimensions?: WizardEvalDimension[];
		selectedCategories?: string[];
	};
	inferencePipeline?: { promptEval?: boolean; config?: WizardPromptEvalConfig };
	scenarioPipeline?: {
		scenarioTestCases?: boolean;
		scenarioEval?: boolean;
		scenarioTestCasesConfig?: WizardScenarioTestCasesConfig;
		scenarioEvalConfig?: WizardScenarioEvalConfig;
		variationDimensions?: unknown;
		judgeDimensions?: WizardJudgeDimension[];
	};
	suiteId?: string;
	runId?: string;
	toolsMode?: 'simulated' | 'real';
	simulatedToolsDescription?: string;
	realToolsYaml?: string;
	realToolsFileName?: string;
}

// ─── Normalize ─────────────────────────────────────────────────────────

export interface NormalizedRun {
	suite: string;
	run: string;
	behaviorName: string;
	configObject: Record<string, unknown>;
	warnings: string[];
}

const DEFAULT_BEHAVIOR_CATEGORY_COUNT = 6;
const RUN_EVAL_CONFIG_FILE = 'eval_config.yaml';
const RUN_LOG_FILE = 'runner.log';
const RUN_PID_FILE = 'runner.pid';

export function normalizeWizardPayload(raw: unknown): NormalizedRun {
	const errors: string[] = [];
	const warnings: string[] = [];

	if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
		throw new WizardValidationError(['Request body must be a JSON object.']);
	}
	const payload = raw as WizardPayload;

	// ── Behavior ──
	const behavior = payload.behavior;
	let behaviorRawName = '';
	let behaviorDefinition = '';
	if (!behavior || typeof behavior !== 'object') {
		errors.push('behavior is required.');
	} else if (behavior.mode === 'existing') {
		behaviorRawName = trimOrEmpty(behavior.name);
		if (!behaviorRawName) {
			errors.push('behavior.name is required when behavior.mode is "existing".');
		}
		behaviorDefinition = trimOrEmpty(behavior.definition);
		if (!behaviorDefinition) {
			warnings.push(
				`Existing behavior "${behaviorRawName}" did not include a definition; generated taxonomy quality may be poor.`
			);
			behaviorDefinition = `Behavior: ${behaviorRawName}\n\n(Imported from a previously-defined suite.)`;
		}
	} else if (behavior.mode === 'create') {
		behaviorRawName = trimOrEmpty(behavior.name);
		behaviorDefinition = trimOrEmpty(behavior.definition);
		if (!behaviorRawName) errors.push('behavior.name is required when behavior.mode is "create".');
		if (!behaviorDefinition) {
			errors.push('behavior.definition is required when behavior.mode is "create".');
		}
	} else {
		errors.push('behavior.mode must be "existing" or "create".');
	}

	const behaviorName = slugifyIdentifier(behaviorRawName);
	if (behaviorRawName && !behaviorName) {
		errors.push(`behavior.name "${behaviorRawName}" could not be converted to a safe identifier.`);
	}

	// ── Target ──
	const evaluationTarget = payload.evaluationTarget;
	if (evaluationTarget !== 'model' && evaluationTarget !== 'agent') {
		errors.push('evaluationTarget must be "model" or "agent".');
	}
	if (evaluationTarget === 'agent') {
		errors.push(
			'evaluationTarget "agent" is not yet supported by the UI submit path. ' +
				'The wizard does not collect a Python callable target. ' +
				'For now, run agent evaluations via the CLI: `assert-eval run --config <config.yaml>`.'
		);
	}

	// ── Tools ──
	const toolsMode = payload.toolsMode;
	const simulatedToolsText = trimOrEmpty(payload.simulatedToolsDescription);
	if (toolsMode === 'real') {
		errors.push(
			'Uploaded "real tools" YAML is not yet wired through. ' +
				'The current backend only accepts {module, toolset, simulator} tool descriptors, ' +
				'and the wizard does not yet capture them. ' +
				'Toggle "Simulated tools" or omit tools for this submission.'
		);
	} else if (toolsMode !== undefined && toolsMode !== 'simulated') {
		errors.push('toolsMode must be "simulated" or "real".');
	}

	// ── Context ──
	const applicationContext = trimOrEmpty(payload.applicationContext);
	if (!applicationContext) {
		errors.push('applicationContext is required.');
	}

	// ── IDs ──
	const userSuiteId = trimOrEmpty(payload.suiteId);
	const existingSuiteId = trimOrEmpty(payload.existingSuiteId);
	const source = payload.source;
	let suite: string;
	if (source === 'existing') {
		if (!existingSuiteId) {
			errors.push('existingSuiteId is required when source is "existing".');
			suite = '';
		} else if (!isSafeArtifactId(existingSuiteId)) {
			errors.push(`existingSuiteId "${existingSuiteId}" is not a safe identifier.`);
			suite = existingSuiteId;
		} else {
			suite = existingSuiteId;
			if (userSuiteId && userSuiteId !== existingSuiteId) {
				errors.push(
					`Suite ID mismatch: existing-suite reuse selected "${existingSuiteId}" but ` +
						`a different suite ID "${userSuiteId}" was typed. Clear the suite ID field ` +
						`or change the existing-suite selection so they agree.`
				);
			}
		}
	} else if (source === 'new') {
		const explicit = userSuiteId || autoSuiteId();
		if (!isSafeArtifactId(explicit)) {
			errors.push(`suiteId "${explicit}" is not a safe identifier.`);
		}
		suite = explicit;
	} else {
		errors.push('source must be "new" or "existing".');
		suite = userSuiteId || autoSuiteId();
	}

	const run = trimOrEmpty(payload.runId);
	if (!run) {
		errors.push('runId is required.');
	} else if (!isSafeArtifactId(run)) {
		errors.push(`runId "${run}" is not a safe identifier.`);
	}

	// ── Which pipelines are enabled? ──
	const promptTestCasesEnabled = payload.testCasesPipeline?.promptTestCases === true;
	const promptEvalEnabled = payload.inferencePipeline?.promptEval === true;
	const scenarioTestCasesEnabled = payload.scenarioPipeline?.scenarioTestCases === true;
	const scenarioEvalEnabled = payload.scenarioPipeline?.scenarioEval === true;
	const systematizeCfg = payload.systematize?.config ?? {};
	const promptTestCasesCfg = payload.testCasesPipeline?.config ?? {};
	const promptEvalCfg = payload.inferencePipeline?.config ?? {};
	const scenarioTestCasesCfg = payload.scenarioPipeline?.scenarioTestCasesConfig ?? {};
	const scenarioCfg = payload.scenarioPipeline?.scenarioEvalConfig ?? {};

	if (!promptTestCasesEnabled && !promptEvalEnabled && !scenarioTestCasesEnabled && !scenarioEvalEnabled) {
		errors.push(
			'At least one pipeline (prompt test cases, prompt eval, scenario test cases, or scenario eval) must be enabled.'
		);
	}
	if (!promptEvalEnabled && !scenarioEvalEnabled) {
		errors.push(
			'At least one run-producing evaluation (prompt eval or scenario eval) must be enabled.'
		);
	}
	if (scenarioEvalEnabled && !scenarioTestCasesEnabled) {
		errors.push('Scenario eval requires scenario test cases to be enabled as well.');
	}
	const scenarioJudgePasses =
		scenarioEvalEnabled && scenarioCfg.judge?.judgePasses !== undefined
			? toIntOrUndef(scenarioCfg.judge.judgePasses)
			: undefined;
	if (scenarioEvalEnabled && scenarioJudgePasses !== undefined && scenarioJudgePasses <= 0) {
		errors.push('scenarioEvalConfig.judge.judgePasses must be greater than 0.');
	}
	if (source === 'new') {
		requireModel(errors, 'systematize.config.model', systematizeCfg.model);
		if (promptTestCasesEnabled) requireModel(errors, 'testCasesPipeline.config.model', promptTestCasesCfg.model);
	}
	if (promptEvalEnabled) {
		requireModel(errors, 'inferencePipeline.config.targetModel', promptEvalCfg.targetModel);
		requireModel(errors, 'inferencePipeline.config.judgeModel', promptEvalCfg.judgeModel);
	}
	if (scenarioTestCasesEnabled) {
		requireModel(errors, 'scenarioPipeline.scenarioTestCasesConfig.model', scenarioTestCasesCfg.model);
	}
	if (scenarioEvalEnabled) {
		requireModel(errors, 'scenarioPipeline.scenarioEvalConfig.target.model', scenarioCfg.target?.model);
		requireModel(
			errors,
			'scenarioPipeline.scenarioEvalConfig.tester.model',
			scenarioCfg.tester?.model
		);
		requireModel(errors, 'scenarioPipeline.scenarioEvalConfig.judge.model', scenarioCfg.judge?.model);
	}

	// Surface every error at once so the user doesn't have to play whack-a-mole.
	if (errors.length > 0) {
		throw new WizardValidationError(errors);
	}

	// ─── Build the eval_config.yaml object ────────────────────────────

	const config: Record<string, unknown> = { suite, run };
	// Single-YAML authoring model — full markdown description lives inline.
	config.behavior = { name: behaviorName, description: behaviorDefinition };

	const contextWithTools = simulatedToolsText
		? `${applicationContext}\n\nTools available to the agent (simulated):\n${simulatedToolsText}`
		: applicationContext;
	config.context = contextWithTools;

	// default_model: prefer explicit choices so generated-mode dimensions have one.
	const defaultModelName =
		trimOrEmpty(promptEvalCfg.targetModel) ||
		trimOrEmpty(systematizeCfg.model) ||
		trimOrEmpty(promptTestCasesCfg.model) ||
		trimOrEmpty(scenarioTestCasesCfg.model) ||
		trimOrEmpty(scenarioCfg.target?.model) ||
		trimOrEmpty(scenarioCfg.tester?.model) ||
		trimOrEmpty(scenarioCfg.judge?.model);
	config.default_model = { name: defaultModelName };

	// Explicit-levels dimensions for the test_set.stratify block.
	const dimensionsBlock = buildDimensionsBlock(
		payload.testCasesPipeline?.dimensionBased === true ? payload.testCasesPipeline?.dimensions : undefined,
		warnings
	);

	const pipeline: Record<string, unknown> = {};

	// systematize: always include — runner short-circuits when outputs exist.
	pipeline.systematize = stripUndefined({
		behavior_category_count:
			toIntOrUndef(systematizeCfg.behaviorCategoryCount) ?? DEFAULT_BEHAVIOR_CATEGORY_COUNT,
		model: buildModelBlock(systematizeCfg)
	});

	// test_set: prompt for query test cases, scenario for scenario test cases,
	// stratify for dimension cross-product.
	const testSetBlock: Record<string, unknown> = {};
	if (dimensionsBlock && dimensionsBlock.length > 0) {
		testSetBlock.stratify = {
			dimensions: dimensionsBlock,
			model: buildModelBlock({ model: defaultModelName })
		};
	}
	if (promptTestCasesEnabled) {
		testSetBlock.prompt = stripUndefined({
			sample_size: toIntOrUndef(promptTestCasesCfg.budget),
			model: buildModelBlock(promptTestCasesCfg)
		});
	}
	if (scenarioTestCasesEnabled) {
		testSetBlock.scenario = stripUndefined({
			sample_size: toIntOrUndef(scenarioTestCasesCfg.budget),
			model: buildModelBlock(scenarioTestCasesCfg)
		});
	}
	if (Object.keys(testSetBlock).length > 0) {
		pipeline.test_set = testSetBlock;
	}

	// inference: target + (scenario only) tester + max_turns.
	const targetModelName =
		(scenarioEvalEnabled && trimOrEmpty(scenarioCfg.target?.model)) ||
		trimOrEmpty(promptEvalCfg.targetModel) ||
		defaultModelName;
	const targetModelExtras = scenarioEvalEnabled ? scenarioCfg.target : undefined;
	const inferenceTarget: Record<string, unknown> = {
		model: buildModelBlock({
			model: targetModelName,
			temperature: targetModelExtras?.temperature,
			maxTokens: targetModelExtras?.maxTokens
		})
	};
	const trimmedSystemPrompt = trimOrEmpty(payload.systemPrompt);
	if (trimmedSystemPrompt) {
		inferenceTarget.system_prompt = trimmedSystemPrompt;
	}

	const inferenceBlock: Record<string, unknown> = { target: inferenceTarget };
	if (scenarioEvalEnabled) {
		if (scenarioCfg.tester) {
			inferenceBlock.tester = stripUndefined({
				model: buildModelBlock({
					model: trimOrEmpty(scenarioCfg.tester.model) || defaultModelName,
					temperature: scenarioCfg.tester.temperature,
					maxTokens: scenarioCfg.tester.maxTokens
				})
			});
		}
		const maxTurns = toIntOrUndef(scenarioCfg.maxTurns);
		if (maxTurns !== undefined) inferenceBlock.max_turns = maxTurns;
	}
	pipeline.inference = inferenceBlock;

	// judge: model + filtered dimensions.
	// Drop dimensions missing description or rubric — the Python validator
	// rejects empty fields, and the wizard's defaults arrive empty until the
	// /api/dimensions merge fills them in.
	const judgeBlock: Record<string, unknown> = {};
	const judgeModelName =
		(scenarioEvalEnabled && trimOrEmpty(scenarioCfg.judge?.model)) ||
		trimOrEmpty(promptEvalCfg.judgeModel) ||
		defaultModelName;
	judgeBlock.model = buildModelBlock({
		model: judgeModelName,
		temperature: scenarioEvalEnabled ? scenarioCfg.judge?.temperature : undefined,
		maxTokens: scenarioEvalEnabled ? scenarioCfg.judge?.maxTokens : undefined
	});
	if (scenarioJudgePasses !== undefined) {
		judgeBlock.n = scenarioJudgePasses;
	}

	const judgeDimensionsRaw = payload.scenarioPipeline?.judgeDimensions ?? [];
	const dimensionsMap: Record<string, { description: string; rubric: string }> = {};
	let droppedDimensions = 0;
	for (const dim of judgeDimensionsRaw) {
		const description = trimOrEmpty(dim?.description);
		const rubric = trimOrEmpty(dim?.rubric);
		if (!dim?.name || !description || !rubric) {
			droppedDimensions += 1;
			continue;
		}
		const key = snakeIdentifier(dim.name);
		if (!key) {
			droppedDimensions += 1;
			continue;
		}
		dimensionsMap[key] = { description, rubric };
	}
	if (droppedDimensions > 0) {
		warnings.push(
			`Dropped ${droppedDimensions} judge dimension(s) with missing description or rubric. ` +
				'Built-in defaults will apply for built-in names.'
		);
	}
	if (Object.keys(dimensionsMap).length > 0) {
		judgeBlock.dimensions = dimensionsMap;
	}
	pipeline.judge = judgeBlock;

	config.pipeline = pipeline;

	return {
		suite,
		run,
		behaviorName,
		configObject: config,
		warnings
	};
}

// ─── File writing ──────────────────────────────────────────────────────

export interface WrittenRun {
	runDir: string;
	configPath: string;
	logPath: string;
	pidPath: string;
}

/**
 * Atomically reserves the run directory and writes eval_config.yaml. The mkdir
 * is the lock: if the directory already exists we refuse rather than overwrite.
 */
export function writeRunConfigFiles(normalized: NormalizedRun): WrittenRun {
	requireSafeId(normalized.suite, 'suite');
	requireSafeId(normalized.run, 'run');

	const suiteDir = suiteDirPath(normalized.suite);
	const runDir = runDirPath(normalized.suite, normalized.run);

	fs.mkdirSync(suiteDir, { recursive: true });

	try {
		fs.mkdirSync(runDir, { recursive: false });
	} catch (err) {
		if ((err as NodeJS.ErrnoException).code === 'EEXIST') {
			throw new RunConflictError(
				`A run directory already exists at ${path.relative(MEASUREMENTS_ROOT, runDir)}. ` +
					'Choose a different run ID, or delete the existing directory if you want to start fresh.'
			);
		}
		throw err;
	}

	const configPath = path.join(runDir, RUN_EVAL_CONFIG_FILE);
	const logPath = path.join(runDir, RUN_LOG_FILE);
	const pidPath = path.join(runDir, RUN_PID_FILE);

	const yamlText = stringifyYaml(normalized.configObject, { lineWidth: 0 });
	fs.writeFileSync(configPath, yamlText, { encoding: 'utf-8' });

	return { runDir, configPath, logPath, pidPath };
}

// ─── Spawn ─────────────────────────────────────────────────────────────

export interface SpawnedRun {
	pid: number;
	command: string;
	args: string[];
}

interface ResolvedCommand {
	command: string;
	args: string[];
	source: string;
}

function pathEnv(): string {
	return process.env.PATH ?? process.env.Path ?? process.env.path ?? '';
}

function pathExts(): string[] {
	if (os.platform() !== 'win32') return [''];

	const raw = process.env.PATHEXT ?? process.env.Pathext ?? '.COM;.EXE';
	const exts = raw
		.split(';')
		.map((ext) => ext.trim())
		.filter(Boolean);
	return exts.length > 0 ? exts : ['.COM', '.EXE'];
}

function findOnPath(command: string): string | undefined {
	if (!command || command.includes(path.sep) || (path.posix.sep !== path.sep && command.includes(path.posix.sep))) {
		return fs.existsSync(command) ? command : undefined;
	}

	const extensions = path.extname(command) ? [''] : pathExts();
	for (const dir of pathEnv().split(path.delimiter).filter(Boolean)) {
		for (const ext of extensions) {
			const candidate = path.join(dir, `${command}${ext}`);
			if (fs.existsSync(candidate)) return candidate;
		}
	}
	return undefined;
}

function venvScriptCandidates(venv: string): string[] {
	return os.platform() === 'win32'
		? [path.join(venv, 'Scripts', 'assert-eval.exe'), path.join(venv, 'Scripts', 'assert-eval')]
		: [path.join(venv, 'bin', 'assert-eval')];
}

function venvPythonCandidates(venv: string): string[] {
	return os.platform() === 'win32'
		? [path.join(venv, 'Scripts', 'python.exe'), path.join(venv, 'Scripts', 'python')]
		: [path.join(venv, 'bin', 'python3'), path.join(venv, 'bin', 'python')];
}

function localVenvCandidates(): string[] {
	return ['.venv', 'venv'].map((name) => path.join(MEASUREMENTS_ROOT, name));
}

function pythonPathCandidates(): { command: string; args: string[]; source: string }[] {
	if (os.platform() === 'win32') {
		return [
			{ command: 'py', args: ['-3'], source: 'Python launcher on PATH' },
			{ command: 'python', args: [], source: 'python on PATH' },
			{ command: 'python3', args: [], source: 'python3 on PATH' }
		];
	}

	return [
		{ command: 'python3', args: [], source: 'python3 on PATH' },
		{ command: 'python', args: [], source: 'python on PATH' }
	];
}

function resolveAssertEvalCommand(configPath: string): ResolvedCommand {
	const cliArgs = ['run', '--config', configPath];

	const override = process.env.ASSERT_EVAL_COMMAND;
	if (override && override.trim()) {
		const parts = override.trim().split(/\s+/);
		return {
			command: parts[0],
			args: [...parts.slice(1), ...cliArgs],
			source: 'ASSERT_EVAL_COMMAND override'
		};
	}

	const venvs = [process.env.VIRTUAL_ENV, ...localVenvCandidates()].filter(
		(venv): venv is string => Boolean(venv && venv.trim())
	);
	for (const venv of venvs) {
		const cliPath = venvScriptCandidates(venv).find((candidate) => fs.existsSync(candidate));
		if (cliPath) {
			return {
				command: cliPath,
				args: cliArgs,
				source: venv === process.env.VIRTUAL_ENV ? `VIRTUAL_ENV (${cliPath})` : `project venv (${cliPath})`
			};
		}
	}

	for (const venv of venvs) {
		const pythonPath = venvPythonCandidates(venv).find((candidate) => fs.existsSync(candidate));
		if (pythonPath) {
			return {
				command: pythonPath,
				args: ['-m', 'assert_eval.cli', ...cliArgs],
				source:
					venv === process.env.VIRTUAL_ENV
						? `VIRTUAL_ENV python (${pythonPath})`
						: `project venv python (${pythonPath})`
			};
		}
	}

	const pathCli = findOnPath('assert-eval');
	if (pathCli) {
		return { command: pathCli, args: cliArgs, source: `PATH (${pathCli})` };
	}

	for (const candidate of pythonPathCandidates()) {
		const pythonPath = findOnPath(candidate.command);
		if (pythonPath) {
			return {
				command: pythonPath,
				args: [...candidate.args, '-m', 'assert_eval.cli', ...cliArgs],
				source: `${candidate.source} (${pythonPath})`
			};
		}
	}

	throw new SpawnError(
		'Could not find a way to start the assert-eval runner. Set ASSERT_EVAL_COMMAND to a working invocation, ' +
			'create/install the project venv, or install Python so the viewer can run `python -m assert_eval.cli` from the repo.'
	);
}

/**
 * Spawn assert-eval detached, wait for the OS to confirm the spawn (or fail).
 * Only after we hear back do we resolve — that way a missing `assert-eval`
 * binary surfaces as a 500 instead of a 200 followed by a forever-pending monitor.
 */
export function spawnAssertEvalRun(written: WrittenRun): Promise<SpawnedRun> {
	let resolved: ResolvedCommand;
	try {
		resolved = resolveAssertEvalCommand(written.configPath);
	} catch (err) {
		return Promise.reject(err);
	}

	let logFd: number;
	try {
		logFd = fs.openSync(written.logPath, 'a');
	} catch (err) {
		return Promise.reject(new SpawnError(`Failed to open log file at ${written.logPath}`, err));
	}

	const preamble =
		`# assert-eval run launched by viewer at ${new Date().toISOString()}\n` +
		`# command: ${resolved.command} ${resolved.args.join(' ')}\n` +
		`# resolved from: ${resolved.source}\n` +
		`# cwd: ${MEASUREMENTS_ROOT}\n` +
		`# ─────────────────────────────────────────────\n`;
	try {
		fs.writeSync(logFd, preamble);
	} catch {
		// best-effort
	}

	return new Promise<SpawnedRun>((resolve, reject) => {
		let child: ChildProcess;
		try {
			child = spawn(resolved.command, resolved.args, {
				cwd: MEASUREMENTS_ROOT,
				env: process.env,
				detached: true,
				stdio: ['ignore', logFd, logFd],
				windowsHide: true
			});
		} catch (err) {
			try {
				fs.closeSync(logFd);
			} catch {
				/* ignore */
			}
			reject(
				new SpawnError(
					`Failed to spawn assert-eval runner via ${resolved.source}: ${(err as Error).message ?? String(err)}`,
					err
				)
			);
			return;
		}

		let settled = false;
		const cleanup = () => {
			child.off('spawn', onSpawn);
			child.off('error', onError);
		};
		const onSpawn = () => {
			if (settled) return;
			settled = true;
			cleanup();
			try {
				fs.closeSync(logFd);
			} catch {
				/* ignore */
			}
			if (child.pid !== undefined) {
				try {
					fs.writeFileSync(written.pidPath, String(child.pid), 'utf-8');
				} catch {
					// PID file is best-effort; don't fail the spawn over it.
				}
			}
			child.unref();
			resolve({ pid: child.pid ?? -1, command: resolved.command, args: resolved.args });
		};
		const onError = (err: Error) => {
			if (settled) return;
			settled = true;
			cleanup();
			try {
				fs.closeSync(logFd);
			} catch {
				/* ignore */
			}
			reject(
				new SpawnError(
					`assert-eval runner failed to start via ${resolved.source}: ${err?.message ?? String(err)}. ` +
						`Ensure the viewer was started in a shell with the project venv activated, ` +
						`or set ASSERT_EVAL_COMMAND to a working invocation (e.g. "assert-eval").`,
					err
				)
			);
		};

		child.on('spawn', onSpawn);
		child.on('error', onError);
	});
}

// ─── Utilities ─────────────────────────────────────────────────────────

function trimOrEmpty(value: unknown): string {
	return typeof value === 'string' ? value.trim() : '';
}

function toIntOrUndef(value: unknown): number | undefined {
	if (value === null || value === undefined) return undefined;
	const n = typeof value === 'number' ? value : Number(value);
	if (!Number.isFinite(n)) return undefined;
	return Math.trunc(n);
}

function toNumberOrUndef(value: unknown): number | undefined {
	if (value === null || value === undefined) return undefined;
	const n = typeof value === 'number' ? value : Number(value);
	if (!Number.isFinite(n)) return undefined;
	return n;
}

function slugifyIdentifier(value: string): string {
	const lowered = value.toLowerCase().trim();
	const replaced = lowered.replace(/[^a-z0-9._-]+/g, '_').replace(/_+/g, '_');
	const stripped = replaced.replace(/^[._-]+/, '').replace(/[._-]+$/, '');
	return isSafeArtifactId(stripped) ? stripped : '';
}

function snakeIdentifier(value: string): string {
	const lowered = value.toLowerCase().trim();
	return lowered
		.replace(/[^a-z0-9_]+/g, '_')
		.replace(/_+/g, '_')
		.replace(/^_+|_+$/g, '');
}

function autoSuiteId(): string {
	const stamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z');
	return `eval-${stamp}`;
}

function stripUndefined(obj: Record<string, unknown>): Record<string, unknown> {
	const out: Record<string, unknown> = {};
	for (const [k, v] of Object.entries(obj)) {
		if (v === undefined) continue;
		if (v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === 0) continue;
		out[k] = v;
	}
	return out;
}

function buildModelBlock(cfg: WizardModelStub | undefined): Record<string, unknown> | undefined {
	if (!cfg) return undefined;
	const name = trimOrEmpty(cfg.model);
	if (!name) return undefined;

	const block: Record<string, unknown> = { name };
	const temperature = toNumberOrUndef(cfg.temperature);
	if (temperature !== undefined) block.temperature = temperature;
	const maxTokens = toIntOrUndef(cfg.maxTokens);
	if (maxTokens !== undefined) block.max_tokens = maxTokens;
	return block;
}

function requireModel(errors: string[], field: string, value: unknown) {
	if (!trimOrEmpty(value)) {
		errors.push(`${field} is required. Configure a model in your environment or add one in the wizard.`);
	}
}

function buildDimensionsBlock(
	factorsRaw: WizardEvalDimension[] | undefined,
	warnings: string[]
): Array<Record<string, unknown>> | null {
	if (!factorsRaw || factorsRaw.length === 0) return null;
	const out: Array<Record<string, unknown>> = [];
	const seen = new Set<string>();
	for (const factor of factorsRaw) {
		const name = slugifyIdentifier(typeof factor?.name === 'string' ? factor.name : '');
		if (!name) {
			warnings.push('Skipped a dimension with an empty/invalid name.');
			continue;
		}
		if (name === 'behavior') {
			warnings.push('Skipped dimension "behavior" — that name is reserved by the stratification stage.');
			continue;
		}
		if (seen.has(name)) {
			warnings.push(`Skipped duplicate dimension "${name}".`);
			continue;
		}
		const rawLevels = Array.isArray(factor.levels) ? factor.levels : [];
		const cleanedLevels: Array<{ name: string; definition: string }> = [];
		const seenSlugs = new Map<string, number>();
		for (const level of rawLevels) {
			const definition = typeof level === 'string' ? level.trim() : '';
			if (!definition) continue;
			let slug = slugifyIdentifier(definition);
			if (!slug) slug = `level_${cleanedLevels.length + 1}`;
			const baseSlug = slug.replace(/_\d+$/, '');
			const existingCount = seenSlugs.get(baseSlug) ?? 0;
			if (existingCount > 0) slug = `${slug}_${existingCount + 1}`;
			seenSlugs.set(baseSlug, existingCount + 1);
			cleanedLevels.push({ name: slug, definition });
		}
		if (cleanedLevels.length < 2) {
			warnings.push(
				`Skipped dimension "${name}" — needs at least 2 non-empty levels (got ${cleanedLevels.length}).`
			);
			continue;
		}
		seen.add(name);
		out.push({ name, levels: cleanedLevels });
	}
	return out;
}

/**
 * Exposed so the +server.ts handler can do a pre-flight existence check
 * without duplicating path logic.
 */
export function runDirExists(suite: string, run: string): boolean {
	if (!isSafeArtifactId(suite) || !isSafeArtifactId(run)) return false;
	return fs.existsSync(runDirPath(suite, run));
}

export const RUN_FILE_NAMES = Object.freeze({
	config: RUN_EVAL_CONFIG_FILE,
	log: RUN_LOG_FILE,
	pid: RUN_PID_FILE
});
