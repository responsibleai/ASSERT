// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import type { DimensionDef, DimensionScales, JudgeStatus, MultiJudge } from './types.js';

type VerdictLike = Record<string, unknown> | null | undefined;

export interface JudgmentRecordLike {
	verdict?: VerdictLike;
	judge_status?: JudgeStatus | string | null;
	judge_error?: string | null;
	score_keys?: string[] | null;
	not_applicable_score_keys?: string[] | null;
	dimension_scales?: DimensionScales | null;
}

export function isBooleanFlag(value: unknown): value is boolean {
	return typeof value === 'boolean';
}

export function getRequiredBaseMetricNames(
	dimensionDefs: Record<string, DimensionDef> | null | undefined
): string[] {
	if (!dimensionDefs) return [];
	return Object.entries(dimensionDefs)
		.filter(([, def]) => def.required_base)
		.map(([name]) => name)
		.sort();
}

function readDimensions(verdict: VerdictLike): Record<string, unknown> | null {
	if (!verdict || typeof verdict !== 'object') return null;
	const dimensions = verdict.dimensions;
	return dimensions && typeof dimensions === 'object' && !Array.isArray(dimensions)
		? (dimensions as Record<string, unknown>)
		: null;
}

export function getVerdictMetricValue(verdict: VerdictLike, metric: string): unknown {
	const dimensions = readDimensions(verdict);
	if (dimensions && metric in dimensions) return dimensions[metric];
	return null;
}

export function getVerdictFlag(verdict: VerdictLike, metric: string): boolean | null {
	const value = getVerdictMetricValue(verdict, metric);
	return isBooleanFlag(value) ? value : null;
}

export function isNotApplicableVerdictDimension(verdict: VerdictLike, metric: string): boolean {
	if (!verdict || typeof verdict !== 'object') return false;
	const dimensions = readDimensions(verdict);
	if (!dimensions || !(metric in dimensions) || dimensions[metric] !== null) return false;
	const applicability = verdict.dimension_applicability;
	return Boolean(
		applicability &&
			typeof applicability === 'object' &&
			!Array.isArray(applicability) &&
			(applicability as Record<string, unknown>)[metric] === false
	);
}

export function getRecordFlag(record: JudgmentRecordLike, metric: string): boolean | null {
	return getVerdictFlag(record.verdict, metric);
}

export function getRecordMetricValue(record: JudgmentRecordLike, metric: string): unknown {
	return getVerdictMetricValue(record.verdict, metric);
}

export function isNotApplicableRecordDimension(record: JudgmentRecordLike, metric: string): boolean {
	return isNotApplicableVerdictDimension(record.verdict, metric);
}

function requiredMetricsForRecord(
	record: JudgmentRecordLike,
	defaultRequiredBaseMetrics: string[]
): string[] {
	const scoreKeys = record.score_keys;
	if (Array.isArray(scoreKeys) && scoreKeys.every((key) => typeof key === 'string')) {
		return [...scoreKeys];
	}
	return defaultRequiredBaseMetrics;
}

function notApplicableMetricsForRecord(record: JudgmentRecordLike): string[] {
	const scoreKeys = record.not_applicable_score_keys;
	if (Array.isArray(scoreKeys) && scoreKeys.every((key) => typeof key === 'string')) {
		return [...scoreKeys];
	}
	return [];
}

function hasSuccessfulJudgeVerdict(
	verdict: VerdictLike,
	requiredMetrics: string[],
	notApplicableMetrics: string[],
	dimensionScales: DimensionScales | null | undefined
): boolean {
	const dimensions = readDimensions(verdict);
	if (dimensions && Array.isArray(verdict?.node_judgments)) {
		return requiredMetrics.every(
			(metric) =>
				isBooleanFlag(dimensions[metric]) ||
				(
					(typeof dimensions[metric] === 'number' || typeof dimensions[metric] === 'string') &&
					Boolean(dimensionScales?.[metric]?.values.some((entry) => entry.value === dimensions[metric]))
				) ||
				(notApplicableMetrics.includes(metric) && isNotApplicableVerdictDimension(verdict, metric))
		);
	}
	return false;
}

export function inferJudgeStatus(
	record: JudgmentRecordLike,
	requiredBaseMetrics: string[]
): JudgeStatus {
	const requiredMetrics = requiredMetricsForRecord(record, requiredBaseMetrics);
	const notApplicableMetrics = notApplicableMetricsForRecord(record);
	if (record.judge_status === 'scoring_skipped') {
		return 'scoring_skipped';
	}
	if (record.judge_status != null) {
		return record.judge_status === 'ok' && hasSuccessfulJudgeVerdict(record.verdict, requiredMetrics, notApplicableMetrics, record.dimension_scales)
			? 'ok'
			: 'judge_failed';
	}
	return hasSuccessfulJudgeVerdict(record.verdict, requiredMetrics, notApplicableMetrics, record.dimension_scales) ? 'ok' : 'judge_failed';
}

export function isSuccessfulJudgment(
	record: JudgmentRecordLike,
	requiredBaseMetrics: string[]
): boolean {
	return inferJudgeStatus(record, requiredBaseMetrics) === 'ok';
}

export function getJudgeError(record: JudgmentRecordLike): string | null {
	if (record.judge_error) return record.judge_error;
	if (record.verdict && typeof record.verdict === 'object') {
		const value = record.verdict.error;
		if (typeof value === 'string' && value) return value;
	}
	return null;
}

export function scoreSortValue(record: JudgmentRecordLike, metric: string): number {
	const rawValue = getRecordMetricValue(record, metric);
	if (typeof rawValue === 'number') return rawValue;
	if (typeof rawValue === 'string') {
		const index = record.dimension_scales?.[metric]?.values.findIndex((entry) => entry.value === rawValue) ?? -1;
		return index >= 0 ? index : Number.MAX_SAFE_INTEGER;
	}
	const value = getRecordFlag(record, metric);
	if (value === null) return 2;
	return value ? 0 : 1;
}

function multiJudgeVotes(
	multiJudge: MultiJudge | null | undefined,
	metric: string
): Array<boolean | number | string | null> {
	const votes = multiJudge?.votes?.[metric];
	return Array.isArray(votes)
		? votes.filter(
				(vote): vote is boolean | number | string | null =>
					vote === null || typeof vote === 'boolean' || typeof vote === 'number' || typeof vote === 'string'
			)
		: [];
}

function multiJudgeMetricNames(
	multiJudge: MultiJudge | null | undefined,
	metrics?: string[]
): string[] {
	if (metrics && metrics.length > 0) return metrics;
	return Object.keys(multiJudge?.votes ?? {});
}

function multiJudgeDimensionAgreement(
	multiJudge: MultiJudge | null | undefined,
	metric: string
): number | null {
	const votes = multiJudgeVotes(multiJudge, metric);
	if (votes.length === 0) return null;
	const counts = new Map<boolean | number | string | null, number>();
	for (const vote of votes) counts.set(vote, (counts.get(vote) ?? 0) + 1);
	const agreeing = Math.max(...counts.values());
	return agreeing / votes.length;
}

export function multiJudgeMeanAgreement(
	multiJudge: MultiJudge | null | undefined,
	metrics?: string[]
): number | null {
	const values = multiJudgeMetricNames(multiJudge, metrics)
		.map((name) => multiJudgeDimensionAgreement(multiJudge, name))
		.filter((value): value is number => value !== null);
	if (values.length === 0) return null;
	return values.reduce((sum, value) => sum + value, 0) / values.length;
}

export function multiJudgeHasDisagreement(
	multiJudge: MultiJudge | null | undefined,
	metrics?: string[]
): boolean {
	return multiJudgeMetricNames(multiJudge, metrics).some((name) => {
		const votes = multiJudgeVotes(multiJudge, name);
		if (votes.length < 2) return false;
		return new Set(votes).size > 1;
	});
}

export function multiJudgeDimensionAgreementLabel(
	multiJudge: MultiJudge | null | undefined,
	metric: string
): string | null {
	const votes = multiJudgeVotes(multiJudge, metric);
	if (votes.length === 0) return null;
	const counts = new Map<boolean | number | string | null, number>();
	for (const vote of votes) counts.set(vote, (counts.get(vote) ?? 0) + 1);
	const agreeing = Math.max(...counts.values());
	return `${agreeing}/${votes.length} agree`;
}
