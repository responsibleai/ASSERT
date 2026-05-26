import {
	getRecordFlag,
	getRequiredBaseMetricNames,
	isBooleanFlag,
	isSuccessfulJudgment
} from '$lib/judgment.js';
import type {
	AuditScore,
	AuditRunMetrics,
	Behavior,
	BinaryCounts,
	DimensionMetrics,
	JudgedSample,
	NodeJudgment,
	RunMetrics
} from '$lib/types.js';
import { loadDimensions } from './dimensions.js';

type EventScoredRecord = {
	verdict?: Record<string, unknown> | null;
};

type EventDimensionAggregate = {
	count: number;
	flagged_count: number;
	clear_count: number;
	counts: BinaryCounts;
};

export function emptyScoreCounts(): BinaryCounts {
	return { 0: 0, 1: 0 };
}

function emptyDimensionAggregate(): EventDimensionAggregate {
	return { count: 0, flagged_count: 0, clear_count: 0, counts: emptyScoreCounts() };
}

function finalizeDimensionAggregate(aggregate: EventDimensionAggregate): DimensionMetrics {
	return {
		rate: aggregate.count > 0 ? aggregate.flagged_count / aggregate.count : 0,
		count: aggregate.count,
		flagged_count: aggregate.flagged_count,
		clear_count: aggregate.clear_count,
		counts: aggregate.counts
	};
}

function readNodeJudgments(verdict: Record<string, unknown> | null | undefined): NodeJudgment[] {
	if (!verdict || typeof verdict !== 'object') return [];
	const nodes = (verdict as Record<string, unknown>).node_judgments;
	return Array.isArray(nodes)
		? nodes.filter(
				(node): node is NodeJudgment =>
					Boolean(node && typeof node === 'object' && !Array.isArray(node))
			)
		: [];
}

function buildPermissibilityIndex(behaviors: Behavior[]): Map<string, boolean> {
	const index = new Map<string, boolean>();
	for (const behavior of behaviors) {
		if (!behavior || typeof behavior.name !== 'string') continue;
		index.set(behavior.name, behavior.permissible === true);
	}
	return index;
}

export function computePolicyViolationByPermissibility(
	records: EventScoredRecord[],
	behaviors: Behavior[]
): { permissible: DimensionMetrics | null; not_permissible: DimensionMetrics | null } {
	if (!behaviors || behaviors.length === 0) {
		return { permissible: null, not_permissible: null };
	}
	const permissibilityIndex = buildPermissibilityIndex(behaviors);
	if (permissibilityIndex.size === 0) {
		return { permissible: null, not_permissible: null };
	}

	const permissible = emptyDimensionAggregate();
	const notPermissible = emptyDimensionAggregate();

	for (const record of records) {
		for (const node of readNodeJudgments(record.verdict)) {
			if (node.relevant !== true) continue;
			if (!isBooleanFlag(node.violated)) continue;
			const name = typeof node.node_name === 'string' ? node.node_name.trim() : '';
			if (!name || !permissibilityIndex.has(name)) continue;
			const bucket = permissibilityIndex.get(name) ? permissible : notPermissible;
			addFlag(bucket, node.violated);
		}
	}

	return {
		permissible: finalizeDimensionAggregate(permissible),
		not_permissible: finalizeDimensionAggregate(notPermissible)
	};
}

function collectDimensionNames(records: EventScoredRecord[]): string[] {
	const names = new Set<string>();
	for (const record of records) {
		const verdict = record.verdict;
		if (!verdict || typeof verdict !== 'object' || Array.isArray(verdict)) continue;
		const dimensions = verdict.dimensions;
		if (!dimensions || typeof dimensions !== 'object' || Array.isArray(dimensions)) continue;
		for (const [name, value] of Object.entries(dimensions)) {
			if (isBooleanFlag(value)) names.add(name);
		}
	}
	return [...names];
}

function initDimensionAggregates(names: string[]): Record<string, EventDimensionAggregate> {
	return Object.fromEntries(
		names.map((name) => [
			name,
			{ count: 0, flagged_count: 0, clear_count: 0, counts: emptyScoreCounts() }
		])
	);
}

function finalizeDimensions(
	aggregates: Record<string, EventDimensionAggregate>
): Record<string, DimensionMetrics> {
	return Object.fromEntries(
		Object.entries(aggregates).map(([name, aggregate]) => [
			name,
			{
				rate: aggregate.count > 0 ? aggregate.flagged_count / aggregate.count : 0,
				count: aggregate.count,
				flagged_count: aggregate.flagged_count,
				clear_count: aggregate.clear_count,
				counts: aggregate.counts
			}
		])
	);
}

function addFlag(aggregate: EventDimensionAggregate, value: boolean): void {
	aggregate.count += 1;
	if (value) {
		aggregate.flagged_count += 1;
		aggregate.counts[1] += 1;
		return;
	}
	aggregate.clear_count += 1;
	aggregate.counts[0] += 1;
}

function dimensionRate(dimensions: Record<string, DimensionMetrics>, name: string): number {
	return dimensions[name]?.rate ?? 0;
}

export function computeAuditRunMetrics(
	scores: AuditScore[],
	behaviors: Behavior[] = []
): AuditRunMetrics | null {
	if (scores.length === 0) return null;

	const requiredBaseMetrics = getRequiredBaseMetricNames(loadDimensions());
	const scoredScores = scores.filter((score) => isSuccessfulJudgment(score, requiredBaseMetrics));
	const dimensionNames = collectDimensionNames(scoredScores);
	const dimensionAggregates = initDimensionAggregates(dimensionNames);
	const counts = emptyScoreCounts();

	for (const score of scoredScores) {
		const policyViolation = getRecordFlag(score, 'policy_violation');
		if (policyViolation !== null) counts[policyViolation ? 1 : 0] += 1;

		for (const dimensionName of dimensionNames) {
			const dimensionFlag = getRecordFlag(score, dimensionName);
			if (dimensionFlag === null) continue;
			addFlag(dimensionAggregates[dimensionName], dimensionFlag);
		}
	}

	const dimensions = finalizeDimensions(dimensionAggregates);
	const total = scores.length;
	const scoredTotal = scoredScores.length;
	const permissibilitySplit = computePolicyViolationByPermissibility(scoredScores, behaviors);

	return {
		total,
		scored_total: scoredTotal,
		judge_failures: total - scoredTotal,
		judge_failure_rate: total > 0 ? (total - scoredTotal) / total : 0,
		counts,
		policy_violation_rate: dimensionRate(dimensions, 'policy_violation'),
		overrefusal_rate: dimensionRate(dimensions, 'overrefusal'),
		policy_violation_on_permissible: permissibilitySplit.permissible,
		policy_violation_on_not_permissible: permissibilitySplit.not_permissible,
		dimensions,
		target: scores[0]?.target ?? '',
		tester_model: scores[0]?.tester_model ?? '',
		judge_model: scores[0]?.judge_model ?? ''
	};
}

export function computeRunMetrics(
	samples: JudgedSample[],
	behaviors: Behavior[] = []
): RunMetrics | null {
	if (samples.length === 0) return null;

	const requiredBaseMetrics = getRequiredBaseMetricNames(loadDimensions());
	const scoredSamples = samples.filter((sample) => isSuccessfulJudgment(sample, requiredBaseMetrics));
	const dimensionNames = collectDimensionNames(scoredSamples);
	const dimensionAggregates = initDimensionAggregates(dimensionNames);
	const counts = emptyScoreCounts();

	for (const sample of scoredSamples) {
		const policyViolation = getRecordFlag(sample, 'policy_violation');
		if (policyViolation !== null) counts[policyViolation ? 1 : 0] += 1;

		for (const dimensionName of dimensionNames) {
			const dimensionFlag = getRecordFlag(sample, dimensionName);
			if (dimensionFlag === null) continue;
			addFlag(dimensionAggregates[dimensionName], dimensionFlag);
		}
	}

	const dimensions = finalizeDimensions(dimensionAggregates);
	const permissibilitySplit = computePolicyViolationByPermissibility(scoredSamples, behaviors);

	return {
		total: samples.length,
		scored_total: scoredSamples.length,
		judge_failures: samples.length - scoredSamples.length,
		judge_failure_rate:
			samples.length > 0 ? (samples.length - scoredSamples.length) / samples.length : 0,
		counts,
		policy_violation_rate: dimensionRate(dimensions, 'policy_violation'),
		overrefusal_rate: dimensionRate(dimensions, 'overrefusal'),
		policy_violation_on_permissible: permissibilitySplit.permissible,
		policy_violation_on_not_permissible: permissibilitySplit.not_permissible,
		target: samples[0]?.target ?? '—',
		judge_model: samples[0]?.judge_model ?? '—',
		dimensions
	};
}
