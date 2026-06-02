// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { error } from '@sveltejs/kit';
import { loadJudgedPrompts, loadAuditScores, loadAuditTranscripts } from '$lib/server/data.js';
import {
	csvResponse,
	detectJudgeDimensions,
	detectStratificationDimensions,
	judgeDimensionValue,
	verdictJustification,
	formatConversation,
	formatAuditTranscript
} from '$lib/server/csv.js';
import type { RequestHandler } from './$types.js';
import type { JudgedSample, AuditScore, AuditTranscript } from '$lib/types.js';

const SAFE_ID = /^[\w.@()-]+$/;

export const GET: RequestHandler = async ({ params, url }) => {
	const { suite_id, run_id } = params;
	if (!SAFE_ID.test(suite_id) || !SAFE_ID.test(run_id)) throw error(400, 'Invalid suite or run ID');
	const type = url.searchParams.get('type') ?? 'results';

	if (type === 'results') {
		const samples = loadJudgedPrompts(suite_id, run_id);
		if (samples.length === 0) throw error(404, 'No judged results found');

		const judgeDims = detectJudgeDimensions(samples.map((s) => s.verdict as Record<string, unknown>));
		const dimensions = detectStratificationDimensions(samples);
		const columns = ['test_case_id', 'prompt', 'response', 'conversation', 'behavior', 'target', 'target_runtime_mode', 'judge_model', 'judge_status', 'judge_error', 'justification', ...dimensions, ...judgeDims];
		const rows = samples.map((s: JudgedSample) => {
			const row: Record<string, unknown> = {
				test_case_id: s.test_case_id ?? '',
				prompt: s.prompt,
				response: s.response,
				conversation: formatConversation(s.messages),
				behavior: s.behavior,
				target: s.target ?? '',
				target_runtime_mode: s.target_runtime_mode ?? '',
				judge_model: s.judge_model ?? '',
				judge_status: s.judge_status ?? '',
				judge_error: s.judge_error ?? '',
				justification: verdictJustification(s.verdict as Record<string, unknown> | null | undefined),
				...Object.fromEntries(dimensions.map((dimension) => [dimension, s.dimensions?.[dimension] ?? '']))
			};
			for (const dim of judgeDims) {
				row[dim] = judgeDimensionValue(s.verdict as Record<string, unknown> | null | undefined, dim);
			}
			return row;
		});
		return csvResponse(`${suite_id}_${run_id}_results.csv`, columns, rows);
	}

	if (type === 'audit_scores') {
		const scores = loadAuditScores(suite_id, run_id);
		if (scores.length === 0) throw error(404, 'No inference scores found');

		const transcripts = loadAuditTranscripts(suite_id, run_id);
		const transcriptMap = new Map<string, AuditTranscript>();
		for (const transcript of transcripts) transcriptMap.set(transcript.test_case_id, transcript);

		const judgeDims = detectJudgeDimensions(scores.map((s) => s.verdict as Record<string, unknown>));
		const dimensions = detectStratificationDimensions(scores);
		const columns = ['test_case_id', 'behavior', 'target', 'tester_model', 'target_runtime_mode', 'judge_model', 'judge_status', 'judge_error', 'justification', 'conversation', 'turns_count', 'stop_reason', ...dimensions, ...judgeDims];
		const rows = scores.map((s: AuditScore) => {
			const row: Record<string, unknown> = {
				test_case_id: s.test_case_id,
				behavior: s.behavior,
				target: s.target ?? '',
				tester_model: s.tester_model ?? '',
				target_runtime_mode: s.target_runtime_mode ?? '',
				judge_model: s.judge_model,
				judge_status: s.judge_status ?? '',
				judge_error: s.judge_error ?? '',
				justification: verdictJustification(s.verdict as Record<string, unknown> | null | undefined),
				conversation: formatAuditTranscript(transcriptMap.get(s.test_case_id)),
				turns_count: s.metadata?.turns_count ?? '',
				stop_reason: s.metadata?.stop_reason ?? '',
				...Object.fromEntries(dimensions.map((dimension) => [dimension, s.dimensions?.[dimension] ?? '']))
			};
			for (const dim of judgeDims) {
				row[dim] = judgeDimensionValue(s.verdict as Record<string, unknown> | null | undefined, dim);
			}
			return row;
		});
		return csvResponse(`${suite_id}_${run_id}_inference_scores.csv`, columns, rows);
	}

	throw error(400, `Unknown export type: ${type}. Use results or audit_scores.`);
};
