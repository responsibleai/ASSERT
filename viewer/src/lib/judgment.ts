// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import type { DimensionDef, JudgeStatus, MultiJudge } from './types.js';

type VerdictLike = Record<string, unknown> | null | undefined;

export interface JudgmentRecordLike {
	verdict?: VerdictLike;
	judge_status?: JudgeStatus | string | null;
	judge_error?: string | null;
	score_keys?: string[] | null;
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

export function getRecordFlag(record: JudgmentRecordLike, metric: string): boolean | null {
	return getVerdictFlag(record.verdict, metric);
}

export function getRecordMetricValue(record: JudgmentRecordLike, metric: string): unknown {
	return getVerdictMetricValue(record.verdict, metric);
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

function hasSuccessfulJudgeVerdict(verdict: VerdictLike, requiredMetrics: string[]): boolean {
	const dimensions = readDimensions(verdict);
	if (dimensions && Array.isArray(verdict?.node_judgments)) {
		return requiredMetrics.every((metric) => isBooleanFlag(dimensions[metric]));
	}
	return false;
}

export function inferJudgeStatus(
	record: JudgmentRecordLike,
	requiredBaseMetrics: string[]
): JudgeStatus {
	const requiredMetrics = requiredMetricsForRecord(record, requiredBaseMetrics);
	if (record.judge_status === 'scoring_skipped') {
		return 'scoring_skipped';
	}
	if (record.judge_status != null) {
		return record.judge_status === 'ok' && hasSuccessfulJudgeVerdict(record.verdict, requiredMetrics)
			? 'ok'
			: 'judge_failed';
	}
	return hasSuccessfulJudgeVerdict(record.verdict, requiredMetrics) ? 'ok' : 'judge_failed';
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
	const value = getRecordFlag(record, metric);
	if (value === null) return 2;
	return value ? 0 : 1;
}

function multiJudgeVotes(multiJudge: MultiJudge | null | undefined, metric: string): boolean[] {
	const votes = multiJudge?.votes?.[metric];
	return Array.isArray(votes) ? votes.filter((vote) => typeof vote === 'boolean') : [];
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
	const trueCount = votes.filter(Boolean).length;
	const agreeing = Math.max(trueCount, votes.length - trueCount);
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
		const trueCount = votes.filter(Boolean).length;
		return trueCount > 0 && trueCount < votes.length;
	});
}

export function multiJudgeDimensionAgreementLabel(
	multiJudge: MultiJudge | null | undefined,
	metric: string
): string | null {
	const votes = multiJudgeVotes(multiJudge, metric);
	if (votes.length === 0) return null;
	const trueCount = votes.filter(Boolean).length;
	const agreeing = Math.max(trueCount, votes.length - trueCount);
	return `${agreeing}/${votes.length} agree`;
}
