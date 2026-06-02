// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import type { DimensionDef, JudgeStatus, MultiJudge } from './types.js';

type VerdictLike = Record<string, unknown> | null | undefined;

export interface JudgmentRecordLike {
	verdict?: VerdictLike;
	judge_status?: JudgeStatus | string | null;
	judge_error?: string | null;
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

function hasSuccessfulJudgeVerdict(verdict: VerdictLike, requiredBaseMetrics: string[]): boolean {
	const dimensions = readDimensions(verdict);
	if (dimensions && Array.isArray(verdict?.node_judgments)) {
		return (
			requiredBaseMetrics.length > 0 &&
			requiredBaseMetrics.every((metric) => isBooleanFlag(dimensions[metric]))
		);
	}
	return false;
}

export function inferJudgeStatus(
	record: JudgmentRecordLike,
	requiredBaseMetrics: string[]
): JudgeStatus {
	if (record.judge_status === 'scoring_skipped') {
		return 'scoring_skipped';
	}
	if (record.judge_status != null) {
		return record.judge_status === 'ok' && hasSuccessfulJudgeVerdict(record.verdict, requiredBaseMetrics)
			? 'ok'
			: 'judge_failed';
	}
	return hasSuccessfulJudgeVerdict(record.verdict, requiredBaseMetrics) ? 'ok' : 'judge_failed';
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

function votesForMetric(
	multiJudge: MultiJudge | null | undefined,
	metric: string
): boolean[] {
	return multiJudge?.votes?.[metric] ?? [];
}

function agreementForVotes(votes: boolean[]): number | null {
	if (votes.length === 0) return null;
	const trueCount = votes.filter(Boolean).length;
	const falseCount = votes.length - trueCount;
	return Math.max(trueCount, falseCount) / votes.length;
}

export function multiJudgeMeanAgreement(
	multiJudge: MultiJudge | null | undefined,
	metrics?: string[]
): number | null {
	if (!multiJudge) return null;
	const names = metrics && metrics.length > 0 ? metrics : Object.keys(multiJudge.votes ?? {});
	const agreements = names.flatMap((metric) => {
		const agreement = agreementForVotes(votesForMetric(multiJudge, metric));
		return agreement == null ? [] : [agreement];
	});
	if (agreements.length > 0) {
		return agreements.reduce((sum, agreement) => sum + agreement, 0) / agreements.length;
	}
	return typeof multiJudge.agreement === 'number' && Number.isFinite(multiJudge.agreement)
		? multiJudge.agreement
		: null;
}

export function multiJudgeHasDisagreement(
	multiJudge: MultiJudge | null | undefined,
	metrics?: string[]
): boolean {
	const meanAgreement = multiJudgeMeanAgreement(multiJudge, metrics);
	return meanAgreement != null && meanAgreement < 1;
}

export function multiJudgeDimensionAgreementLabel(
	multiJudge: MultiJudge | null | undefined,
	metric: string
): string | null {
	const votes = votesForMetric(multiJudge, metric);
	if (votes.length === 0) return null;
	const trueCount = votes.filter(Boolean).length;
	const agreeingSamples = Math.max(trueCount, votes.length - trueCount);
	return `${agreeingSamples}/${votes.length} agree`;
}
