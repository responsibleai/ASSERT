import { ARTIFACTS_ROOT } from './config.js';
import { loadDimensions } from './dimensions.js';
import {
	RUN_CONFIG_FILE,
	RUN_MANIFEST_FILE,
	ViewerReadModelError,
	loadIndexedRunScoreRow,
	loadIndexedRunTranscriptRow,
	loadRunRuntimeMode,
	loadRunScoreRow,
	loadRunTranscriptRow,
	type RunSnapshot,
	type SuiteSnapshot,
	type UnifiedSeedRow,
	type UnifiedScoreRow,
	type UnifiedTranscriptRow,
	loadViewerRunIndexes,
	loadViewerRunReadModel,
	loadRunSnapshot,
	loadSuiteSnapshot,
	listSubdirectories,
	readJsonFile,
	readYamlFile,
	runDirPath
} from './artifacts.js';
import {
	computeAuditRunMetrics,
	computeRunMetrics,
	emptyScoreCounts
} from './metrics.js';
import { getRecordFlag } from '$lib/judgment.js';
import { normalizePromptResult, normalizeScenarioResult } from '$lib/result-view.js';
import type {
	AuditRunListItem,
	AuditRunMetrics,
	AuditScore,
	AuditTranscript,
	BinaryCounts,
	DimensionMetrics,
	LlmCallTrace,
	PromptSeed,
	InteractionMessage,
	JudgeStatus,
	JudgedSample,
	Manifest,
	MultiJudge,
	Taxonomy,
	RunListItem,
	RunMetrics,
	ScenarioSeed,
	ScenarioSeedInfo,
	Suite,
	SuiteListItem,
	SuiteStatus,
	Behavior,
	ViewerResultItem
} from '$lib/types.js';

interface PromptMetricView {
	total: number;
	scoredTotal: number;
	judgeFailures: number;
	judgeFailureRate: number;
	counts: BinaryCounts;
	policyViolationRate: number;
	overrefusalRate: number;
	dimensions: Record<string, DimensionMetrics>;
	target: string;
	judge_model: string;
}

interface AuditMetricView {
	total: number;
	scoredTotal: number;
	judgeFailures: number;
	judgeFailureRate: number;
	counts: BinaryCounts;
	policyViolationRate: number;
	overrefusalRate: number;
	dimensions: Record<string, DimensionMetrics>;
	target: string;
	tester_model: string;
	judge_model: string;
}

interface InferencePreviewRow {
	test_case_id: string;
	behavior: string;
	turns_count: number;
	stop_reason: string;
}

interface CompareDimensionSummary {
	rate: number;
	counts: BinaryCounts;
	n: number;
}

interface CompareRunSummary {
	run_id: string;
	display_name: string;
	model: string;
	judge_model: string;
	date: string;
	total: number;
	scoredTotal: number;
	judgeFailures: number;
	judgeFailureRate: number;
	policyViolationRate: number;
	overrefusalRate: number;
	counts: BinaryCounts;
	dimensions: Record<string, CompareDimensionSummary>;
	samples: JudgedSample[];
	meanAgreement: number | null;
	highVarianceCount: number;
}

interface CompareMetricSummary {
	rate: number;
	counts: BinaryCounts;
	n: number;
}

interface BehaviorComparison {
	behavior: string;
	metrics: Record<string, Record<string, CompareMetricSummary>>;
	deltas: Record<string, number>;
}

function hasKind(row: Record<string, unknown>, expected: 'prompt' | 'scenario'): boolean {
	return (row.type ?? row.kind) === expected;
}

function readObject(value: unknown): Record<string, unknown> | null {
	return value && typeof value === 'object' && !Array.isArray(value)
		? (value as Record<string, unknown>)
		: null;
}

function readSeedPayload(row: UnifiedSeedRow | undefined): Record<string, unknown> | null {
	return readObject(row?.seed);
}

function normalizeFactorValue(value: string): string {
	return value
		.replace(/_/g, ' ')
		.split(' ')
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(' ');
}

function readFactors(value: unknown): Record<string, string> | undefined {
	const record = readObject(value);
	if (!record) return undefined;
	const dimensions = Object.fromEntries(Object.entries(record).filter((entry): entry is [string, string] => {
		const [name, dimension] = entry;
		return typeof name === 'string' && typeof dimension === 'string';
	}).map(([name, dimension]) => [name, name === 'behavior' ? dimension : normalizeFactorValue(dimension)]));
	return Object.keys(dimensions).length > 0 ? dimensions : undefined;
}

function readBehavior(value: unknown): string {
	const dimensions = readFactors(value);
	return typeof dimensions?.behavior === 'string' ? dimensions.behavior : '';
}

function readRowBehavior(row: { behavior?: unknown; dimensions?: unknown } | undefined): string {
	return readBehavior(row?.dimensions);
}

function behaviorDefinition(taxonomy: Taxonomy | null, behavior: string): string {
	const entry = taxonomy?.behavior_categories?.find((item) => item.name === behavior);
	if (!entry) throw new Error(`behavior '${behavior}' is missing from taxonomy.behavior_categories`);
	return entry.definition;
}


function normalizeBehavior(b: Behavior): Behavior {
	return { ...b, permissible: b.permissible ?? false };
}

function normalizePolicy(taxonomy: Taxonomy | null | undefined): Taxonomy | null {
	if (!taxonomy) return null;
	const behavior = taxonomy.behavior ?? taxonomy.risk;
	return {
		...taxonomy,
		behavior: behavior ?? { name: '', definition: '' },
		behavior_categories: (taxonomy.behavior_categories ?? []).map(normalizeBehavior)
	};
}

function normalizePromptSeed(item: PromptSeed, taxonomy: Taxonomy | null): PromptSeed {
	const dimensions = readFactors(item.dimensions);
	const behavior = readRowBehavior(item);
	if (!behavior) throw new Error(`seed '${item.test_case_id}' is missing dimensions.behavior`);
	return { ...item, behavior, definition: behaviorDefinition(taxonomy, behavior), dimensions };
}

function normalizeScenarioSeed(item: ScenarioSeed, taxonomy: Taxonomy | null): ScenarioSeed {
	const dimensions = readFactors(item.dimensions);
	const behavior = readRowBehavior(item);
	if (!behavior) throw new Error(`seed '${item.test_case_id}' is missing dimensions.behavior`);
	return { ...item, behavior, definition: behaviorDefinition(taxonomy, behavior), dimensions };
}

function normalizeJudgedSample(sample: JudgedSample): JudgedSample {
	return sample;
}

function normalizeAuditScore(score: AuditScore): AuditScore {
	return score;
}

function normalizeAuditTranscript(transcript: AuditTranscript): AuditTranscript {
	return {
		...transcript,
		behavior: readRowBehavior(transcript),
		dimensions: readFactors(transcript.dimensions)
	};
}

function formatToolArgs(value: unknown): string {
	if (value === null) return 'null';
	if (typeof value === 'string') return JSON.stringify(value);
	if (typeof value === 'number' || typeof value === 'boolean') return String(value);
	if (Array.isArray(value)) return `[${value.map((item) => formatToolArgs(item)).join(', ')}]`;
	if (value && typeof value === 'object') {
		const entries = Object.entries(value as Record<string, unknown>).map(
			([key, item]) => `${JSON.stringify(key)}: ${formatToolArgs(item)}`
		);
		return `{${entries.join(', ')}}`;
	}
	return 'null';
}

function formatToolCallContent(toolName: string, toolArgs: Record<string, unknown>, toolResult: unknown): string {
	return `[Tool call: ${toolName}(${formatToolArgs(toolArgs)}) → ${typeof toolResult === 'string' ? toolResult : ''}]`;
}

function suiteSeedCounts(seedRows: UnifiedSeedRow[]): { prompt: number; scenario: number } {
	return seedRows.reduce(
		(counts: { prompt: number; scenario: number }, row) => {
			if (hasKind(row, 'prompt')) counts.prompt += 1;
			if (hasKind(row, 'scenario')) counts.scenario += 1;
			return counts;
		},
		{ prompt: 0, scenario: 0 }
	);
}

function countConversationMessages(messages: InteractionMessage[]): number {
	const turns = new Set<number>();
	for (const message of messages) {
		if (typeof message.judgeTurn === 'number') turns.add(message.judgeTurn);
	}
	return turns.size;
}

function countTargetConversationMessages(transcript: UnifiedTranscriptRow): number {
	return countConversationMessages(materializeTargetMessages(transcript));
}

function readLlmCalls(value: unknown): LlmCallTrace[] {
	if (!Array.isArray(value)) return [];
	return value.flatMap((item) => {
		if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
		const raw = item as Record<string, unknown>;
		if (typeof raw.call_id !== 'string' || typeof raw.source !== 'string' || typeof raw.api_mode !== 'string') {
			return [];
		}
		const messageIds = Array.isArray(raw.message_ids)
			? raw.message_ids.filter((entry): entry is string => typeof entry === 'string')
			: [];
		return [{
			call_id: raw.call_id,
			source: raw.source,
			api_mode: raw.api_mode,
			request: raw.request,
			response: raw.response,
			derived: readObject(raw.derived) ?? undefined,
			message_ids: messageIds
		}];
	});
}

function agentLabelFromRaw(raw: Record<string, unknown> | undefined): string | null {
	if (!raw) return null;
	const node = raw._node;
	if (typeof node !== 'string') return null;
	const trimmed = node.trim();
	return trimmed || null;
}

function materializeTargetMessages(transcript: UnifiedTranscriptRow): InteractionMessage[] {
	const messages: InteractionMessage[] = [];
	const events = Array.isArray(transcript.events) ? transcript.events : [];
	let judgeTurn = 0;
	let lastPrincipalRole: 'user' | 'assistant' | null = null;

	for (const [eventIndex, event] of events.entries()) {
		if (!event || typeof event !== 'object') continue;

		const rawViewField = (event as Record<string, unknown>).view;
		const rawViews = Array.isArray(rawViewField) ? rawViewField : [rawViewField];
		const views = rawViews.filter((view): view is string => typeof view === 'string');
		if (!views.includes('target')) continue;

		const edit = (event as Record<string, unknown>).edit;
		if (!edit || typeof edit !== 'object') continue;

		const kind = (edit as Record<string, unknown>).type;
		const raw =
			(event as Record<string, unknown>).raw &&
			typeof (event as Record<string, unknown>).raw === 'object' &&
			!Array.isArray((event as Record<string, unknown>).raw)
				? ((event as Record<string, unknown>).raw as Record<string, unknown>)
				: undefined;
		const id = `event:${eventIndex}`;
		const agent = agentLabelFromRaw(raw);

		if (kind === 'add_message' || kind === 'set_system_message') {
			const payload = (edit as Record<string, unknown>).message;
			if (!payload || typeof payload !== 'object') continue;

			const role = (payload as Record<string, unknown>).role;
			const content = (payload as Record<string, unknown>).content;
			const toolCalls = Array.isArray((payload as Record<string, unknown>).tool_calls)
				? ((payload as Record<string, unknown>).tool_calls as InteractionMessage['tool_calls'])
				: undefined;
			const toolCallId =
				typeof (payload as Record<string, unknown>).tool_call_id === 'string'
					? ((payload as Record<string, unknown>).tool_call_id as string)
					: undefined;
			const functionName =
				typeof (payload as Record<string, unknown>).function === 'string'
					? ((payload as Record<string, unknown>).function as string)
					: undefined;
			const argumentsObject =
				(payload as Record<string, unknown>).arguments &&
				typeof (payload as Record<string, unknown>).arguments === 'object' &&
				!Array.isArray((payload as Record<string, unknown>).arguments)
					? ((payload as Record<string, unknown>).arguments as Record<string, unknown>)
					: undefined;
			if (typeof role !== 'string' || typeof content !== 'string') continue;

			let messageJudgeTurn: number | null;
			if (kind === 'set_system_message' || role === 'system') {
				messageJudgeTurn = null;
			} else if (role === 'user') {
				judgeTurn += 1;
				messageJudgeTurn = judgeTurn;
				lastPrincipalRole = 'user';
			} else if (role === 'assistant' || role === 'tool') {
				if (lastPrincipalRole !== 'assistant') judgeTurn += 1;
				messageJudgeTurn = judgeTurn;
				lastPrincipalRole = 'assistant';
			} else {
				messageJudgeTurn = null;
			}

			messages.push({
				id,
				role: role as InteractionMessage['role'],
				content,
				type: kind === 'set_system_message' ? 'set_system_message' : 'message',
				judgeTurn: messageJudgeTurn,
				tool_calls: toolCalls,
				tool_call_id: toolCallId,
				function: functionName,
				arguments: argumentsObject,
				agent: role === 'assistant' ? agent : null,
				raw
			});
			continue;
		}

		if (kind !== 'tool_call') continue;

		const toolName = (edit as Record<string, unknown>).tool_name;
		const toolArgs =
			(edit as Record<string, unknown>).tool_args &&
			typeof (edit as Record<string, unknown>).tool_args === 'object' &&
			!Array.isArray((edit as Record<string, unknown>).tool_args)
				? ((edit as Record<string, unknown>).tool_args as Record<string, unknown>)
				: {};
		const toolCallId =
			typeof (edit as Record<string, unknown>).tool_call_id === 'string'
				? ((edit as Record<string, unknown>).tool_call_id as string)
				: undefined;
		const toolResult = (edit as Record<string, unknown>).tool_result;
		if (typeof toolName !== 'string') continue;
		if (lastPrincipalRole !== 'assistant') judgeTurn += 1;
		lastPrincipalRole = 'assistant';
		messages.push({
			id,
			role: 'tool',
			content: formatToolCallContent(toolName, toolArgs, toolResult),
			type: 'tool_call',
			judgeTurn,
			tool_call_id: toolCallId,
			function: toolName,
			arguments: toolArgs,
			agent: null,
			raw
		});
	}

	return messages;
}

function promptSeedRows(seedRows: UnifiedSeedRow[]): UnifiedSeedRow[] {
	return seedRows.filter((row) => hasKind(row, 'prompt'));
}

function scenarioSeedRows(seedRows: UnifiedSeedRow[]): UnifiedSeedRow[] {
	return seedRows.filter((row) => hasKind(row, 'scenario'));
}

function buildJudgedSampleRow(
	runId: string,
	runtimeMode: string | null,
	seedRow: UnifiedSeedRow | undefined,
	scoreRow: UnifiedScoreRow,
	transcriptRow: UnifiedTranscriptRow | undefined
): JudgedSample {
	const seedMetadata = readSeedPayload(seedRow);
	const messages = transcriptRow ? materializeTargetMessages(transcriptRow) : [];
	const prompt = messages.find((message) => message.role === 'user')?.content ?? '';
	const response =
		[...messages]
			.reverse()
			.find((message) => message.role === 'assistant' && message.content.trim().length > 0)?.content ?? '';
	const verdict =
		scoreRow.verdict && typeof scoreRow.verdict === 'object' && !Array.isArray(scoreRow.verdict)
			? (scoreRow.verdict as JudgedSample['verdict'])
			: null;

	return normalizeJudgedSample({
		test_case_id: typeof scoreRow.test_case_id === 'string' ? scoreRow.test_case_id : undefined,
		prompt,
		response,
		behavior: readRowBehavior(scoreRow) || readRowBehavior(transcriptRow),
		run_id: runId,
		judge_model: typeof scoreRow.judge_model === 'string' ? scoreRow.judge_model : undefined,
		target:
			typeof scoreRow.target === 'string'
				? scoreRow.target
				: typeof transcriptRow?.target === 'string'
					? transcriptRow.target
					: undefined,
		seed_metadata: seedMetadata,
		verdict,
		judge_status:
			typeof scoreRow.judge_status === 'string' ? (scoreRow.judge_status as JudgeStatus) : null,
		judge_error: typeof scoreRow.judge_error === 'string' ? scoreRow.judge_error : null,
		messages,
		llm_calls: readLlmCalls(transcriptRow?.llm_calls),
		target_runtime_mode: runtimeMode,
		dimensions: readFactors(scoreRow.dimensions) ?? readFactors(transcriptRow?.dimensions) ?? readFactors(seedRow?.dimensions),
		multi_judge:
			scoreRow.multi_judge &&
			typeof scoreRow.multi_judge === 'object' &&
			!Array.isArray(scoreRow.multi_judge)
				? (scoreRow.multi_judge as MultiJudge)
				: undefined
	});
}

function buildJudgedPromptsFromSnapshot(snapshot: RunSnapshot): JudgedSample[] {
	return buildJudgedSamplesByKind(snapshot, 'prompt');
}

function buildJudgedScenariosFromSnapshot(snapshot: RunSnapshot): JudgedSample[] {
	return buildJudgedSamplesByKind(snapshot, 'scenario');
}

function buildJudgedSamplesByKind(
	snapshot: RunSnapshot,
	kind: 'prompt' | 'scenario'
): JudgedSample[] {
	const scoreRows = snapshot.scoreRows.filter((row) => hasKind(row, kind));
	const seedRows = snapshot.seedRows.filter((row) => hasKind(row, kind));
	const transcriptRows = snapshot.transcriptRows.filter((row) => hasKind(row, kind));

	const seedById = new Map<string, UnifiedSeedRow>();
	for (const seedRow of seedRows) {
		const seedId = typeof seedRow.test_case_id === 'string' ? seedRow.test_case_id : '';
		if (seedId) seedById.set(seedId, seedRow);
	}

	const transcriptBySeedId = new Map<string, UnifiedTranscriptRow>();
	for (const transcriptRow of transcriptRows) {
		const seedId = typeof transcriptRow.test_case_id === 'string' ? transcriptRow.test_case_id : '';
		if (seedId) transcriptBySeedId.set(seedId, transcriptRow);
	}

	return scoreRows.map((row) => {
		const seedId = typeof row.test_case_id === 'string' ? row.test_case_id : '';
		return buildJudgedSampleRow(
			snapshot.runId,
			snapshot.runtimeMode,
			seedById.get(seedId),
			row,
			transcriptBySeedId.get(seedId)
		);
	});
}

function buildAuditScoreRow(
	runtimeMode: string | null,
	scoreRow: UnifiedScoreRow,
	transcriptRow: UnifiedTranscriptRow | undefined
): AuditScore {
	const turnsCount = transcriptRow ? countTargetConversationMessages(transcriptRow) : 0;
	const stopReason = typeof transcriptRow?.stop_reason === 'string' ? transcriptRow.stop_reason : '';
	const dimensions = readFactors(scoreRow.dimensions) ?? readFactors(transcriptRow?.dimensions);

	return normalizeAuditScore({
		...(scoreRow as AuditScore & UnifiedScoreRow),
		behavior: readRowBehavior(scoreRow) || readRowBehavior(transcriptRow),
		target_runtime_mode: runtimeMode,
		dimensions,
		metadata: {
			turns_count: turnsCount,
			stop_reason: stopReason
		}
	});
}

function buildAuditScoresFromSnapshot(snapshot: RunSnapshot): AuditScore[] {
	const transcriptRows = snapshot.transcriptRows.filter((row) => hasKind(row, 'scenario'));
	const transcriptBySeedId = new Map<string, UnifiedTranscriptRow>();
	for (const transcriptRow of transcriptRows) {
		const seedId = typeof transcriptRow.test_case_id === 'string' ? transcriptRow.test_case_id : '';
		if (seedId) transcriptBySeedId.set(seedId, transcriptRow);
	}

	return snapshot.scoreRows
		.filter((row): row is AuditScore & UnifiedScoreRow => hasKind(row, 'scenario'))
		.map((row) => {
			const seedId = typeof row.test_case_id === 'string' ? row.test_case_id : '';
			return buildAuditScoreRow(snapshot.runtimeMode, row, transcriptBySeedId.get(seedId));
		});
}

function buildPromptMetricSamplesFromScoreRows(
	runId: string,
	runtimeMode: string | null,
	scoreRows: UnifiedScoreRow[]
): JudgedSample[] {
	return scoreRows
		.filter((row) => hasKind(row, 'prompt'))
		.map((row) => buildJudgedSampleRow(runId, runtimeMode, undefined, row, undefined));
}

function buildAuditMetricScoresFromScoreRows(
	runtimeMode: string | null,
	scoreRows: UnifiedScoreRow[]
): AuditScore[] {
	return scoreRows
		.filter((row): row is AuditScore & UnifiedScoreRow => hasKind(row, 'scenario'))
		.map((row) => buildAuditScoreRow(runtimeMode, row, undefined));
}

function buildAuditTranscriptsFromSnapshot(snapshot: RunSnapshot): AuditTranscript[] {
	return snapshot.transcriptRows
		.filter((row): row is AuditTranscript & UnifiedTranscriptRow => hasKind(row, 'scenario'))
		.map((row) => normalizeAuditTranscript(row));
}

function buildInferencePreviewRowsFromSnapshot(snapshot: RunSnapshot): InferencePreviewRow[] {
	if (snapshot.manifest?.stages?.inference !== 'running') return [];

	return snapshot.transcriptRows
		.filter((row): row is UnifiedTranscriptRow => hasKind(row, 'scenario'))
		.flatMap((row) => {
			const seedId = typeof row.test_case_id === 'string' ? row.test_case_id : '';
			if (!seedId) return [];

			const messages = materializeTargetMessages(row);
			return [{
				test_case_id: seedId,
				behavior: readRowBehavior(row),
				turns_count: countConversationMessages(messages),
				stop_reason: typeof row.stop_reason === 'string' ? row.stop_reason : ''
			}];
			});
}

function buildScenarioDrawerItem(
	runtimeMode: string | null,
	transcriptRow: UnifiedTranscriptRow,
	scoreRow: UnifiedScoreRow | undefined,
	seedInfo: ScenarioSeedInfo | undefined
): ViewerResultItem | null {
	const seedId = typeof transcriptRow.test_case_id === 'string' ? transcriptRow.test_case_id : '';
	if (!seedId) return null;

	const turnsCount = countTargetConversationMessages(transcriptRow);
	const stopReason = typeof transcriptRow.stop_reason === 'string' ? transcriptRow.stop_reason : '';
	const matchedScore = scoreRow ? buildAuditScoreRow(runtimeMode, scoreRow, transcriptRow) : null;
	const score: AuditScore = matchedScore
		? matchedScore
		: {
				test_case_id: seedId,
				behavior: readRowBehavior(transcriptRow),
				judge_model: '',
				target: typeof transcriptRow.target === 'string' ? transcriptRow.target : undefined,
				tester_model:
					typeof transcriptRow.tester_model === 'string' ? transcriptRow.tester_model : undefined,
				verdict: null,
				judge_status: null,
				judge_error: null,
				target_runtime_mode: runtimeMode,
				dimensions: readFactors(transcriptRow.dimensions) ?? seedInfo?.dimensions,
				metadata: {
					turns_count: turnsCount,
					stop_reason: stopReason
				}
			};

	return normalizeScenarioResult(
		score,
		materializeTargetMessages(transcriptRow),
		readLlmCalls(transcriptRow.llm_calls),
		seedInfo
	);
}

function buildRunListEntries(snapshot: SuiteSnapshot): {
	runs: RunListItem[];
	auditRuns: AuditRunListItem[];
} {
	const runs: RunListItem[] = [];
	const auditRuns: AuditRunListItem[] = [];

	for (const runId of snapshot.runIds) {
		const runSnapshot = loadRunSnapshot(snapshot.suiteId, runId, snapshot.seedRows, {
			includeTranscripts: false
		});
		const manifest = runSnapshot.manifest;
		const promptScores = buildPromptMetricSamplesFromScoreRows(
			runSnapshot.runId,
			runSnapshot.runtimeMode,
			runSnapshot.scoreRows
		);
		const auditScores = buildAuditMetricScoresFromScoreRows(runSnapshot.runtimeMode, runSnapshot.scoreRows);

		const hasPromptScores = promptScores.length > 0;
		const hasAuditScores = auditScores.length > 0;
		const hasScoreStage = manifest?.stages?.judge != null;

		if ((hasPromptScores || hasScoreStage) && !(manifest?.status === 'failed' && !hasPromptScores)) {
			runs.push({
				run_id: runId,
				has_judged: hasPromptScores,
				has_scenario_scores: hasAuditScores,
				manifest,
				metrics: hasPromptScores ? computeRunMetrics(promptScores) : null
			});
		}

		if ((hasAuditScores || hasScoreStage) && !(manifest?.status === 'failed' && !hasAuditScores)) {
			auditRuns.push({
				run_id: runId,
				has_scores: hasAuditScores,
				manifest,
				metrics: hasAuditScores ? computeAuditRunMetrics(auditScores) : null
			});
		}
	}

	return { runs, auditRuns };
}

function buildZeroPromptMetrics(): PromptMetricView {
	return {
		total: 0,
		scoredTotal: 0,
		judgeFailures: 0,
		judgeFailureRate: 0,
		counts: emptyScoreCounts(),
		policyViolationRate: 0,
		overrefusalRate: 0,
		dimensions: {},
		target: '',
		judge_model: ''
	};
}

function buildZeroAuditMetrics(): AuditMetricView {
	return {
		total: 0,
		scoredTotal: 0,
		judgeFailures: 0,
		judgeFailureRate: 0,
		counts: emptyScoreCounts(),
		policyViolationRate: 0,
		overrefusalRate: 0,
		dimensions: {},
		target: '',
		tester_model: '',
		judge_model: ''
	};
}

function toPromptMetricView(metrics: RunMetrics | null): PromptMetricView {
	if (!metrics) return buildZeroPromptMetrics();
	return {
		total: metrics.total,
		scoredTotal: metrics.scored_total,
		judgeFailures: metrics.judge_failures,
		judgeFailureRate: metrics.judge_failure_rate,
		counts: metrics.counts,
		policyViolationRate: metrics.policy_violation_rate,
		overrefusalRate: metrics.overrefusal_rate,
		dimensions: metrics.dimensions,
		target: metrics.target,
		judge_model: metrics.judge_model
	};
}

function toAuditMetricView(metrics: AuditRunMetrics | null): AuditMetricView {
	if (!metrics) return buildZeroAuditMetrics();
	return {
		total: metrics.total,
		scoredTotal: metrics.scored_total,
		judgeFailures: metrics.judge_failures,
		judgeFailureRate: metrics.judge_failure_rate,
		counts: metrics.counts,
		policyViolationRate: metrics.policy_violation_rate,
		overrefusalRate: metrics.overrefusal_rate,
		dimensions: metrics.dimensions,
		target: metrics.target,
		tester_model: metrics.tester_model,
		judge_model: metrics.judge_model
	};
}

function buildScenarioSeedMap(
	scenarioSeeds: ScenarioSeed[],
	auditScores: AuditScore[]
): Record<string, ScenarioSeedInfo> {
	const auditScoresBySeedId = new Map(auditScores.map((score) => [score.test_case_id, score]));

	return Object.fromEntries(
		scenarioSeeds.map((scenarioSeed) => {
			const score = auditScoresBySeedId.get(scenarioSeed.test_case_id);
			return [
				scenarioSeed.test_case_id,
				{
					title: scenarioSeed.seed.title,
					description: scenarioSeed.seed.description,
					tools: scenarioSeed.seed.tools,
					dimensions: scenarioSeed.dimensions,
					target_runtime_mode:
						typeof score?.target_runtime_mode === 'string' ? score.target_runtime_mode : null
				}
			];
		})
	);
}

function buildScenarioSeedInfo(
	scenarioSeeds: ScenarioSeed[],
	seedId: string,
	auditScores: AuditScore[]
): ScenarioSeedInfo | undefined {
	const scenarioSeed = scenarioSeeds.find((item) => item.test_case_id === seedId);
	if (!scenarioSeed) return undefined;
	const score = auditScores.find((item) => item.test_case_id === seedId);
	return {
		title: scenarioSeed.seed.title,
		description: scenarioSeed.seed.description,
		tools: scenarioSeed.seed.tools,
		dimensions: scenarioSeed.dimensions,
		target_runtime_mode: typeof score?.target_runtime_mode === 'string' ? score.target_runtime_mode : null
	};
}

function buildMultiJudgeStats(samples: JudgedSample[], auditScores: AuditScore[]) {
	const promptMultiJudge = samples.filter((sample) => sample.multi_judge);
	const auditMultiJudge = auditScores.filter((score) => score.multi_judge);
	const agreements = [
		...promptMultiJudge.map((sample) => sample.multi_judge!.agreement),
		...auditMultiJudge.map((score) => score.multi_judge!.agreement)
	];
	if (agreements.length === 0) return null;

	return {
		total: agreements.length,
		judgeN: promptMultiJudge[0]?.multi_judge?.n ?? auditMultiJudge[0]?.multi_judge?.n ?? 0,
		meanAgreement: agreements.reduce((sum, agreement) => sum + agreement, 0) / agreements.length,
		unanimous: agreements.filter((agreement) => agreement === 1).length,
		split: agreements.filter((agreement) => agreement > 0.5 && agreement < 1).length,
		highVariance: agreements.filter((agreement) => agreement <= 0.5).length
	};
}

function formatRunDate(manifest: Manifest | null): string {
	if (!manifest?.started_at) return '—';
	const value =
		typeof manifest.started_at === 'number'
			? new Date(manifest.started_at * 1000)
			: new Date(manifest.started_at);
	return value.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function buildCompareRunSummary(runId: string, manifest: Manifest | null, samples: JudgedSample[]): CompareRunSummary {
	const metrics = computeRunMetrics(samples);
	if (!metrics) {
		throw new Error(`No judged samples for run "${runId}"`);
	}

	const dimensions: CompareRunSummary['dimensions'] = Object.fromEntries(
		Object.entries(metrics.dimensions).map(([name, value]) => [
			name,
			{
				rate: value.rate,
				counts: value.counts,
				n: value.count
			}
		])
	);

	const multiJudgeSamples = samples.filter((sample) => sample.multi_judge);
	const meanAgreement =
		multiJudgeSamples.length > 0
			? multiJudgeSamples.reduce((sum, sample) => sum + sample.multi_judge!.agreement, 0) /
				multiJudgeSamples.length
			: null;

	return {
		run_id: runId,
		display_name: runId,
		model: metrics.target,
		judge_model: metrics.judge_model,
		date: formatRunDate(manifest),
		total: metrics.total,
		scoredTotal: metrics.scored_total,
		judgeFailures: metrics.judge_failures,
		judgeFailureRate: metrics.judge_failure_rate,
		policyViolationRate: metrics.policy_violation_rate,
		overrefusalRate: metrics.overrefusal_rate,
		counts: metrics.counts,
		dimensions,
		samples,
		meanAgreement,
		highVarianceCount: multiJudgeSamples.filter((sample) => sample.multi_judge!.agreement <= 0.5).length
	};
}

function buildBehaviorComparisons(
	runSummaries: CompareRunSummary[],
	runIds: string[],
	allMetrics: string[]
): {
	comparisons: BehaviorComparison[];
	samplesByBehavior: Record<string, Record<string, JudgedSample[]>>;
} {
	const comparisonByBehavior = new Map<string, BehaviorComparison>();
	const samplesByBehavior: Record<string, Record<string, JudgedSample[]>> = {};

	for (const run of runSummaries) {
		const grouped = new Map<string, JudgedSample[]>();
		for (const sample of run.samples) {
			if (!grouped.has(sample.behavior)) grouped.set(sample.behavior, []);
			grouped.get(sample.behavior)!.push(sample);

			samplesByBehavior[sample.behavior] ??= {};
			samplesByBehavior[sample.behavior][run.run_id] ??= [];
			samplesByBehavior[sample.behavior][run.run_id].push(sample);
		}

		for (const [behavior, samples] of grouped) {
			if (!comparisonByBehavior.has(behavior)) {
				comparisonByBehavior.set(behavior, {
					behavior,
					metrics: Object.fromEntries(allMetrics.map((metric) => [metric, {}])),
					deltas: {}
				});
			}

			const comparison = comparisonByBehavior.get(behavior)!;
			for (const metric of allMetrics) {
				const scores = emptyScoreCounts();
				let count = 0;

				for (const sample of samples) {
					const value = getRecordFlag(sample, metric);
					if (value === null) continue;
					scores[value ? 1 : 0] += 1;
					count += 1;
				}

				comparison.metrics[metric][run.run_id] = {
					rate: count > 0 ? scores[1] / count : 0,
					counts: scores,
					n: count
				};
			}
		}
	}

	const comparisons = Array.from(comparisonByBehavior.values());
	for (const comparison of comparisons) {
		for (const metric of allMetrics) {
			const first = comparison.metrics[metric]?.[runIds[0]];
			const last = comparison.metrics[metric]?.[runIds[runIds.length - 1]];
			comparison.deltas[metric] = (last?.rate ?? 0) - (first?.rate ?? 0);
		}
	}

	comparisons.sort(
		(left, right) =>
			Math.abs(right.deltas.policy_violation ?? 0) - Math.abs(left.deltas.policy_violation ?? 0)
	);

	return { comparisons, samplesByBehavior };
}

export function listSuites(): SuiteListItem[] {
	return listSubdirectories(ARTIFACTS_ROOT)
		.map((suiteId) => loadSuiteListItem(suiteId))
		.filter((suite): suite is SuiteListItem => suite !== null);
}

function loadSuiteListItem(suiteId: string): SuiteListItem | null {
	const snapshot = loadSuiteSnapshot(suiteId);
	if (!snapshot) return null;

	const itemCounts = suiteSeedCounts(snapshot.seedRows);
	let evalRunCount = 0;
	let hasResults = false;

	for (const runId of snapshot.runIds) {
		const runSnapshot = loadRunSnapshot(suiteId, runId, snapshot.seedRows, {
			includeTranscripts: false
		});
		const promptRows = runSnapshot.scoreRows.filter((row) => hasKind(row, 'prompt'));
		const scenarioRows = runSnapshot.scoreRows.filter((row) => hasKind(row, 'scenario'));
		const hasData = promptRows.length > 0 || scenarioRows.length > 0;
		const hasEvalStage =
			runSnapshot.manifest?.stages?.inference != null || runSnapshot.manifest?.stages?.judge != null;
		if (!hasData && !hasEvalStage) continue;
		if (!hasData && runSnapshot.manifest?.status === 'failed') continue;
		evalRunCount += 1;
		if (hasData) hasResults = true;
	}

	let status: SuiteStatus = 'systematized';
	if (hasResults) status = 'has_results';
	else if (itemCounts.prompt > 0 || itemCounts.scenario > 0) status = 'test_set_ready';

	return {
		suite_id: suiteId,
		behavior_name: normalizePolicy(snapshot.taxonomy)?.behavior?.name || suiteId,
		behavior_category_count: snapshot.taxonomy?.behavior_categories?.length ?? 0,
		prompt_test_case_count: itemCounts.prompt,
		scenario_test_case_count: itemCounts.scenario,
		run_count: evalRunCount,
		runs: snapshot.runIds,
		status,
		created_at: snapshot.suite?.created_at ?? '',
		has_systematization: snapshot.systematization !== null
	};
}

function buildPromptSeeds(snapshot: SuiteSnapshot | null): PromptSeed[] {
	if (!snapshot) return [];
	return promptSeedRows(snapshot.seedRows).map((row) =>
		normalizePromptSeed(row as unknown as PromptSeed, snapshot.taxonomy)
	);
}

function buildPromptSeedTitleMap(snapshot: SuiteSnapshot | null): Record<string, string> {
	if (!snapshot) return {};
	const map: Record<string, string> = {};
	for (const row of promptSeedRows(snapshot.seedRows)) {
		const seed = row as unknown as PromptSeed;
		if (seed.test_case_id && seed.seed?.title) map[seed.test_case_id] = seed.seed.title;
	}
	return map;
}

function buildScenarioSeeds(snapshot: SuiteSnapshot | null): ScenarioSeed[] {
	if (!snapshot) return [];
	return scenarioSeedRows(snapshot.seedRows).map((row) =>
		normalizeScenarioSeed(row as unknown as ScenarioSeed, snapshot.taxonomy)
	);
}

export function loadPolicy(suiteId: string): Taxonomy | null {
	const snapshot = loadSuiteSnapshot(suiteId);
	return normalizePolicy(snapshot?.taxonomy);
}

export function loadPromptSeeds(suiteId: string): PromptSeed[] {
	return buildPromptSeeds(loadSuiteSnapshot(suiteId));
}

export function loadScenarioSeeds(suiteId: string): ScenarioSeed[] {
	return buildScenarioSeeds(loadSuiteSnapshot(suiteId));
}

export function loadSuite(suiteId: string): Suite | null {
	return loadSuiteSnapshot(suiteId)?.suite ?? null;
}

export function loadSystematization(suiteId: string): Record<string, unknown> | null {
	return loadSuiteSnapshot(suiteId)?.systematization ?? null;
}

export function listRuns(suiteId: string): RunListItem[] {
	const snapshot = loadSuiteSnapshot(suiteId);
	if (!snapshot) return [];
	return buildRunListEntries(snapshot).runs;
}

export function listAuditRuns(suiteId: string): AuditRunListItem[] {
	const snapshot = loadSuiteSnapshot(suiteId);
	if (!snapshot) return [];
	return buildRunListEntries(snapshot).auditRuns;
}

export function loadJudgedPrompts(suiteId: string, runId: string): JudgedSample[] {
	return buildJudgedPromptsFromSnapshot(loadRunSnapshot(suiteId, runId));
}

export function loadJudgedScenarios(suiteId: string, runId: string): JudgedSample[] {
	return buildJudgedScenariosFromSnapshot(loadRunSnapshot(suiteId, runId));
}

export function loadAuditScores(suiteId: string, runId: string): AuditScore[] {
	return buildAuditScoresFromSnapshot(loadRunSnapshot(suiteId, runId));
}

export function loadAuditTranscripts(suiteId: string, runId: string): AuditTranscript[] {
	return buildAuditTranscriptsFromSnapshot(loadRunSnapshot(suiteId, runId));
}

export function loadManifest(suiteId: string, runId: string): Manifest | null {
	return loadRunSnapshot(suiteId, runId).manifest;
}

export interface SuiteHeavyData {
	runs: RunListItem[];
	auditRuns: AuditRunListItem[];
	evalCountsByBehavior: Record<string, number>;
	primaryEvalRunId: string | null;
	primaryRunPromptsByBehavior: Record<string, JudgedSample[]>;
	primaryRunScenariosByBehavior: Record<string, JudgedSample[]>;
}

async function loadSuiteHeavyData(
	suiteId: string,
	snapshot: SuiteSnapshot
): Promise<SuiteHeavyData> {
	const runs: RunListItem[] = [];
	const auditRuns: AuditRunListItem[] = [];
	const evalCountsByBehavior: Record<string, number> = {};
	let primaryEvalRunId: string | null = null;
	let primaryRunSnapshot: RunSnapshot | null = null;

	for (const runId of snapshot.runIds) {
		const runSnapshot = loadRunSnapshot(suiteId, runId, snapshot.seedRows, {
			includeTranscripts: false
		});
		const manifest = runSnapshot.manifest;
		const promptScores = buildPromptMetricSamplesFromScoreRows(
			runSnapshot.runId,
			runSnapshot.runtimeMode,
			runSnapshot.scoreRows
		);
		const auditScores = buildAuditMetricScoresFromScoreRows(
			runSnapshot.runtimeMode,
			runSnapshot.scoreRows
		);

		const hasPromptScores = promptScores.length > 0;
		const hasAuditScores = auditScores.length > 0;
		const hasScoreStage = manifest?.stages?.judge != null;

		const addedToRuns =
			(hasPromptScores || hasScoreStage) &&
			!(manifest?.status === 'failed' && !hasPromptScores);
		const addedToAuditRuns =
			(hasAuditScores || hasScoreStage) &&
			!(manifest?.status === 'failed' && !hasAuditScores);

		if (addedToRuns) {
			runs.push({
				run_id: runId,
				has_judged: hasPromptScores,
				has_scenario_scores: hasAuditScores,
				manifest,
				metrics: hasPromptScores ? computeRunMetrics(promptScores) : null
			});
		}
		if (addedToAuditRuns) {
			auditRuns.push({
				run_id: runId,
				has_scores: hasAuditScores,
				manifest,
				metrics: hasAuditScores ? computeAuditRunMetrics(auditScores) : null
			});
		}

		if (hasPromptScores || hasAuditScores) {
			for (const row of runSnapshot.scoreRows) {
				const behavior = readRowBehavior(row);
				if (!behavior) continue;
				evalCountsByBehavior[behavior] = (evalCountsByBehavior[behavior] ?? 0) + 1;
			}
		}

		if (primaryEvalRunId === null && (addedToRuns || addedToAuditRuns)) {
			primaryEvalRunId = runId;
			primaryRunSnapshot = runSnapshot;
		}
	}

	const primaryRunPromptsByBehavior: Record<string, JudgedSample[]> = primaryRunSnapshot
		? groupSamplesByBehavior(buildJudgedPromptsFromSnapshot(primaryRunSnapshot))
		: {};
	const primaryRunScenariosByBehavior: Record<string, JudgedSample[]> = primaryRunSnapshot
		? groupSamplesByBehavior(buildJudgedScenariosFromSnapshot(primaryRunSnapshot))
		: {};

	return {
		runs,
		auditRuns,
		evalCountsByBehavior,
		primaryEvalRunId,
		primaryRunPromptsByBehavior,
		primaryRunScenariosByBehavior
	};
}

export function loadSuitePageData(suiteId: string) {
	const snapshot = loadSuiteSnapshot(suiteId);
	if (!snapshot) return null;

	const promptSeeds = buildPromptSeeds(snapshot);
	const scenarioSeeds = buildScenarioSeeds(snapshot);

	return {
		suite_id: suiteId,
		suite: snapshot.suite,
		taxonomy: normalizePolicy(snapshot.taxonomy),
		promptSeeds,
		scenarioSeeds,
		dimensionDefs: loadDimensions(),
		systematization: snapshot.systematization,
		streamed: {
			heavy: loadSuiteHeavyData(suiteId, snapshot)
		}
	};
}

function groupSamplesByBehavior(samples: JudgedSample[]): Record<string, JudgedSample[]> {
	const grouped: Record<string, JudgedSample[]> = {};
	for (const sample of samples) {
		const key = sample.behavior ?? '';
		(grouped[key] ??= []).push(sample);
	}
	return grouped;
}

function loadRunManifestRecord(suiteId: string, runId: string): Manifest | null {
	return readJsonFile<Manifest>(`${runDirPath(suiteId, runId)}/${RUN_MANIFEST_FILE}`, { missingOk: true });
}

function loadRuntimeModeForRun(suiteId: string, runId: string): string | null {
	return loadRunRuntimeMode(
		readYamlFile<Record<string, unknown>>(`${runDirPath(suiteId, runId)}/${RUN_CONFIG_FILE}`, {
			missingOk: true
		})
	);
}

function hasCompletedJudge(manifest: Manifest | null): boolean {
	return manifest?.stages?.judge === 'completed';
}

function loadCompletedRunPageData(
	suiteId: string,
	runId: string,
	suiteSnapshot: SuiteSnapshot | null,
	manifest: Manifest | null,
	activeTab: 'prompts' | 'audit'
) {
	const viewerReadModel = loadViewerRunReadModel(suiteId, runId);
	const promptRows = viewerReadModel.promptRows.map((row) =>
		normalizeJudgedSample(row as unknown as JudgedSample)
	);
	const auditRows = viewerReadModel.auditRows.map((row) =>
		normalizeAuditScore(row as unknown as AuditScore)
	);
	const promptCount = promptRows.length;
	const auditCount = auditRows.length;
	const hasAuditContent = auditCount > 0;
	const resolvedTab = activeTab === 'prompts' && promptCount === 0 && hasAuditContent ? 'audit' : activeTab;
	const samples = resolvedTab === 'prompts' ? promptRows : [];
	const auditScores = resolvedTab === 'audit' ? auditRows : [];
	const scenarioSeeds = buildScenarioSeeds(suiteSnapshot);
	const promptMetrics = resolvedTab === 'prompts' ? computeRunMetrics(samples) : null;
	const auditMetrics = resolvedTab === 'audit' ? computeAuditRunMetrics(auditScores) : null;

	return {
		suite_id: suiteId,
		run_id: runId,
		activeTab: resolvedTab,
		promptCount,
		auditCount,
		hasAuditContent,
		manifest,
		taxonomy: normalizePolicy(suiteSnapshot?.taxonomy),
		samples,
		auditScores,
		inferencePreviewRows: [],
		scenarioDrawerItems: {},
		inferencePreviewTotal: scenarioSeeds.length,
		scenarioSeedMap: buildScenarioSeedMap(scenarioSeeds, auditRows),
		promptSeedTitleMap: buildPromptSeedTitleMap(suiteSnapshot),
		dimensionDefs: loadDimensions(),
		multiJudgeStats: buildMultiJudgeStats(samples, auditScores),
		metrics: toPromptMetricView(promptMetrics),
		auditMetrics: toAuditMetricView(auditMetrics)
	};
}

export function loadRunPageData(suiteId: string, runId: string, activeTab: 'prompts' | 'audit' = 'prompts') {
	const suiteSnapshot = loadSuiteSnapshot(suiteId);
	const manifest = loadRunManifestRecord(suiteId, runId);
	if (hasCompletedJudge(manifest)) {
		try {
			return loadCompletedRunPageData(suiteId, runId, suiteSnapshot, manifest, activeTab);
		} catch (err) {
			if (!(err instanceof ViewerReadModelError)) throw err;
			// Stale or missing read model — fall through to raw-file path
		}
	}

	let resolvedTab = activeTab;
	let runSnapshot = loadRunSnapshot(suiteId, runId, suiteSnapshot?.seedRows, {
		transcriptKind: activeTab === 'audit' ? 'scenario' : 'prompt'
	});
	const promptCount = runSnapshot.scoreRows.filter((row) => hasKind(row, 'prompt')).length;
	const auditCount = runSnapshot.scoreRows.filter((row) => hasKind(row, 'scenario')).length;
	const hasAuditContent = auditCount > 0 || runSnapshot.manifest?.stages?.inference === 'running';

	if (resolvedTab === 'prompts' && promptCount === 0 && hasAuditContent) {
		resolvedTab = 'audit';
		runSnapshot = loadRunSnapshot(suiteId, runId, suiteSnapshot?.seedRows, {
			transcriptKind: 'scenario'
		});
	}

	const samples = resolvedTab === 'prompts' ? buildJudgedPromptsFromSnapshot(runSnapshot) : [];
	const auditScores = resolvedTab === 'audit' ? buildAuditScoresFromSnapshot(runSnapshot) : [];
	const inferencePreviewRows =
		resolvedTab === 'audit' && auditScores.length === 0
			? buildInferencePreviewRowsFromSnapshot(runSnapshot)
			: [];

	if (!runSnapshot.manifest && promptCount === 0 && auditCount === 0 && inferencePreviewRows.length === 0) {
		return null;
	}

	const scenarioSeeds = buildScenarioSeeds(suiteSnapshot);
	const promptSeedTitleMap = buildPromptSeedTitleMap(suiteSnapshot);
	const promptMetrics = resolvedTab === 'prompts' ? computeRunMetrics(samples) : null;
	const auditMetrics = resolvedTab === 'audit' ? computeAuditRunMetrics(auditScores) : null;
	const scenarioSeedMap = resolvedTab === 'audit' ? buildScenarioSeedMap(scenarioSeeds, auditScores) : {};

	return {
		suite_id: suiteId,
		run_id: runId,
		activeTab: resolvedTab,
		promptCount,
		auditCount,
		hasAuditContent,
		manifest: runSnapshot.manifest,
		taxonomy: normalizePolicy(suiteSnapshot?.taxonomy),
		samples,
		auditScores,
		inferencePreviewRows,
		scenarioDrawerItems: {},
		inferencePreviewTotal: scenarioSeeds.length,
		scenarioSeedMap,
		promptSeedTitleMap,
		dimensionDefs: loadDimensions(),
		multiJudgeStats: buildMultiJudgeStats(samples, auditScores),
		metrics: toPromptMetricView(promptMetrics),
		auditMetrics: toAuditMetricView(auditMetrics)
	};
}

function findSeedRowById(seedRows: UnifiedSeedRow[], seedId: string): UnifiedSeedRow | undefined {
	return seedRows.find((row) => row.test_case_id === seedId);
}

function loadPromptDrawerItemFromReadModel(suiteId: string, runId: string, seedId: string) {
	const suiteSnapshot = loadSuiteSnapshot(suiteId);
	const viewerReadModel = loadViewerRunIndexes(suiteId, runId);
	const transcriptRow = loadIndexedRunTranscriptRow(
		suiteId,
		runId,
		viewerReadModel.transcriptIndex,
		seedId,
		'prompt'
	);
	const scoreRow = loadIndexedRunScoreRow(suiteId, runId, viewerReadModel.scoreIndex, seedId, 'prompt');
	if (!transcriptRow || !scoreRow) return null;

	const sample = buildJudgedSampleRow(
		runId,
		loadRuntimeModeForRun(suiteId, runId),
		findSeedRowById(suiteSnapshot?.seedRows ?? [], seedId),
		scoreRow,
		transcriptRow
	);
	return normalizePromptResult(sample);
}

async function loadPromptDrawerItemFromCanonical(suiteId: string, runId: string, seedId: string) {
	const suiteSnapshot = loadSuiteSnapshot(suiteId);
	const [transcriptRow, scoreRow] = await Promise.all([
		loadRunTranscriptRow(suiteId, runId, seedId, 'prompt'),
		loadRunScoreRow(suiteId, runId, seedId, 'prompt')
	]);
	if (!transcriptRow) return null;
	if (!scoreRow) return null;

	const sample = buildJudgedSampleRow(
		runId,
		loadRuntimeModeForRun(suiteId, runId),
		findSeedRowById(suiteSnapshot?.seedRows ?? [], seedId),
		scoreRow,
		transcriptRow
	);
	return normalizePromptResult(sample);
}

function loadScenarioDrawerItemFromReadModel(suiteId: string, runId: string, seedId: string) {
	const suiteSnapshot = loadSuiteSnapshot(suiteId);
	const viewerReadModel = loadViewerRunIndexes(suiteId, runId);
	const transcriptRow = loadIndexedRunTranscriptRow(
		suiteId,
		runId,
		viewerReadModel.transcriptIndex,
		seedId,
		'scenario'
	);
	const scoreRow = loadIndexedRunScoreRow(suiteId, runId, viewerReadModel.scoreIndex, seedId, 'scenario');
	if (!transcriptRow || !scoreRow) return null;

	const auditScore = buildAuditScoreRow(loadRuntimeModeForRun(suiteId, runId), scoreRow, transcriptRow);
	const scenarioSeeds = buildScenarioSeeds(suiteSnapshot);
	return normalizeScenarioResult(
		auditScore,
		materializeTargetMessages(transcriptRow),
		readLlmCalls(transcriptRow.llm_calls),
		buildScenarioSeedInfo(scenarioSeeds, seedId, [])
	);
}

export async function loadPromptDrawerItem(suiteId: string, runId: string, seedId: string) {
	if (hasCompletedJudge(loadRunManifestRecord(suiteId, runId))) {
		try {
			return loadPromptDrawerItemFromReadModel(suiteId, runId, seedId);
		} catch (err) {
			if (!(err instanceof ViewerReadModelError)) throw err;
		}
	}
	return loadPromptDrawerItemFromCanonical(suiteId, runId, seedId);
}

export async function loadScenarioDrawerItem(suiteId: string, runId: string, seedId: string) {
	if (hasCompletedJudge(loadRunManifestRecord(suiteId, runId))) {
		try {
			return loadScenarioDrawerItemFromReadModel(suiteId, runId, seedId);
		} catch (err) {
			if (!(err instanceof ViewerReadModelError)) throw err;
		}
	}

	const suiteSnapshot = loadSuiteSnapshot(suiteId);
	const [transcriptRow, matchedScoreRow] = await Promise.all([
		loadRunTranscriptRow(suiteId, runId, seedId, 'scenario'),
		loadRunScoreRow(suiteId, runId, seedId, 'scenario')
	]);
	if (!transcriptRow) return null;

	const runtimeMode = loadRuntimeModeForRun(suiteId, runId);
	const scenarioSeeds = buildScenarioSeeds(suiteSnapshot);
	return buildScenarioDrawerItem(
		runtimeMode,
		transcriptRow,
		matchedScoreRow ?? undefined,
		buildScenarioSeedInfo(
			scenarioSeeds,
			seedId,
			matchedScoreRow ? [buildAuditScoreRow(runtimeMode, matchedScoreRow, transcriptRow)] : []
		)
	);
}

export function loadComparePageData(suiteId: string, runIds: string[]) {
	const suiteSnapshot = loadSuiteSnapshot(suiteId);
	const taxonomy = normalizePolicy(suiteSnapshot?.taxonomy);

	const runSummaries: CompareRunSummary[] = [];
	const metricNames = new Set<string>();

	for (const runId of runIds) {
		const runSnapshot = loadRunSnapshot(suiteId, runId, suiteSnapshot?.seedRows);
		const samples = buildJudgedPromptsFromSnapshot(runSnapshot);
		if (samples.length === 0) return null;

		const summary = buildCompareRunSummary(runId, runSnapshot.manifest, samples);
		for (const dimensionName of Object.keys(summary.dimensions)) metricNames.add(dimensionName);
		runSummaries.push(summary);
	}

	const allMetrics = Array.from(metricNames);
	const { comparisons, samplesByBehavior } = buildBehaviorComparisons(runSummaries, runIds, allMetrics);

	return {
		suite_id: suiteId,
		taxonomy,
		runs: runSummaries.map(({ samples, ...summary }) => summary),
		comparisons,
		samplesByBehavior,
		allMetrics,
		dimensionDefs: loadDimensions()
	};
}
