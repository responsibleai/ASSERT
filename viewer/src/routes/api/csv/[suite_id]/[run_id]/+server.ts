// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { error } from '@sveltejs/kit';
import { loadJudgedPrompts, loadAuditScores, loadAuditTranscripts } from '$lib/server/data.js';
import { csvResponse } from '$lib/server/csv.js';
import type { RequestHandler } from './$types.js';
import type { AuditScore, AuditTranscript, InteractionMessage, JudgedSample } from '$lib/types.js';

const SAFE_ID = /^[\w.@()-]+$/;

function detectScoreDimensions(verdicts: Array<Record<string, unknown> | null | undefined>): string[] {
	const dimensions = new Set<string>();
	for (const verdict of verdicts) {
		if (!verdict) continue;
		if (verdict.dimensions && typeof verdict.dimensions === 'object' && !Array.isArray(verdict.dimensions)) {
			for (const key of Object.keys(verdict.dimensions)) dimensions.add(key);
		}
		for (const [key, value] of Object.entries(verdict)) {
			if (
				key === 'dimensions' ||
				key === 'justification' ||
				key === 'confidence' ||
				key === 'citations' ||
				key.endsWith('_justification')
			) {
				continue;
			}
			if ((typeof value === 'number' && value >= 1 && value <= 3) || typeof value === 'boolean') {
				dimensions.add(key);
			}
		}
		if (dimensions.size > 0) break;
	}
	return [...dimensions].sort();
}

function detectVariations(items: Array<{ dimensions?: Record<string, string> }>): string[] {
	const dimensions = new Set<string>();
	for (const item of items) {
		for (const key of Object.keys(item.dimensions ?? {})) dimensions.add(key);
	}
	return [...dimensions].sort();
}

function dimensionValue(verdict: Record<string, unknown> | null | undefined, dimension: string): unknown {
	const dimensions = verdict?.dimensions;
	if (dimensions && typeof dimensions === 'object' && !Array.isArray(dimensions) && dimension in dimensions) {
		return (dimensions as Record<string, unknown>)[dimension];
	}
	return verdict?.[dimension] ?? '';
}

function verdictJustification(verdict: Record<string, unknown> | null | undefined): string {
	return typeof verdict?.justification === 'string' ? verdict.justification : '';
}

function formatMessages(messages: InteractionMessage[] | undefined): string {
	if (!messages?.length) return '';
	return messages
		.map((message) => {
			const role = message.role.toUpperCase();
			if (message.tool_calls?.length) {
				const calls = message.tool_calls
					.map((toolCall) => `${toolCall.function}(${JSON.stringify(toolCall.arguments)})`)
					.join('; ');
				return `[${role}]: ${message.content || ''}\n  tool_calls: ${calls}`;
			}
			if (message.role === 'tool') {
				return `[TOOL ${message.function ?? ''}]: ${message.content}`;
			}
			return `[${role}]: ${message.content}`;
		})
		.join('\n');
}

function formatResultTranscript(transcript: AuditTranscript | undefined): string {
	if (!transcript?.events?.length) return '';
	return transcript.events
		.map((event) => {
			if (event.edit?.type === 'add_message' && event.edit.message) {
				return `[${event.edit.message.role.toUpperCase()}]: ${event.edit.message.content}`;
			}
			if (event.edit?.type === 'tool_call' && event.edit.tool_name) {
				const args = event.edit.tool_args ? JSON.stringify(event.edit.tool_args) : '{}';
				return `[TOOL ${event.edit.tool_name}]: (${args}) -> ${event.edit.tool_result || ''}`;
			}
			return null;
		})
		.filter(Boolean)
		.join('\n');
}

export const GET: RequestHandler = async ({ params, url }) => {
	const { suite_id, run_id } = params;
	if (!SAFE_ID.test(suite_id) || !SAFE_ID.test(run_id)) throw error(400, 'Invalid suite or run ID');
	const type = url.searchParams.get('type') ?? 'results';

	if (type === 'results') {
		const samples = loadJudgedPrompts(suite_id, run_id);
		if (samples.length === 0) throw error(404, 'No judged results found');

		const scoreDimensions = detectScoreDimensions(samples.map((sample) => sample.verdict as Record<string, unknown>));
		const variations = detectVariations(samples);
		const columns = [
			'test_case_id',
			'prompt',
			'response',
			'messages',
			'behavior',
			'target',
			'target_runtime_mode',
			'judge_model',
			'judge_status',
			'judge_error',
			'justification',
			...variations,
			...scoreDimensions
		];
		const rows = samples.map((sample: JudgedSample) => {
			const row: Record<string, unknown> = {
				test_case_id: sample.test_case_id ?? '',
				prompt: sample.prompt,
				response: sample.response,
				messages: formatMessages(sample.messages),
				behavior: sample.behavior,
				target: sample.target ?? '',
				target_runtime_mode: sample.target_runtime_mode ?? '',
				judge_model: sample.judge_model ?? '',
				judge_status: sample.judge_status ?? '',
				judge_error: sample.judge_error ?? '',
				justification: verdictJustification(sample.verdict as Record<string, unknown> | null | undefined),
				...Object.fromEntries(variations.map((name) => [name, sample.dimensions?.[name] ?? '']))
			};
			for (const dimension of scoreDimensions) {
				row[dimension] = dimensionValue(sample.verdict as Record<string, unknown> | null | undefined, dimension);
			}
			return row;
		});
		return csvResponse(`${suite_id}_${run_id}_results.csv`, columns, rows);
	}

	if (type === 'scenario_results') {
		const scores = loadAuditScores(suite_id, run_id);
		if (scores.length === 0) throw error(404, 'No scenario results found');

		const transcripts = loadAuditTranscripts(suite_id, run_id);
		const transcriptMap = new Map<string, AuditTranscript>();
		for (const transcript of transcripts) transcriptMap.set(transcript.test_case_id, transcript);

		const scoreDimensions = detectScoreDimensions(scores.map((score) => score.verdict as Record<string, unknown>));
		const variations = detectVariations(scores);
		const columns = [
			'test_case_id',
			'behavior',
			'target',
			'tester_model',
			'target_runtime_mode',
			'judge_model',
			'judge_status',
			'judge_error',
			'justification',
			'transcript',
			'turns_count',
			'stop_reason',
			...variations,
			...scoreDimensions
		];
		const rows = scores.map((score: AuditScore) => {
			const row: Record<string, unknown> = {
				test_case_id: score.test_case_id,
				behavior: score.behavior,
				target: score.target ?? '',
				tester_model: score.tester_model ?? '',
				target_runtime_mode: score.target_runtime_mode ?? '',
				judge_model: score.judge_model,
				judge_status: score.judge_status ?? '',
				judge_error: score.judge_error ?? '',
				justification: verdictJustification(score.verdict as Record<string, unknown> | null | undefined),
				transcript: formatResultTranscript(transcriptMap.get(score.test_case_id)),
				turns_count: score.metadata?.turns_count ?? '',
				stop_reason: score.metadata?.stop_reason ?? '',
				...Object.fromEntries(variations.map((name) => [name, score.dimensions?.[name] ?? '']))
			};
			for (const dimension of scoreDimensions) {
				row[dimension] = dimensionValue(score.verdict as Record<string, unknown> | null | undefined, dimension);
			}
			return row;
		});
		return csvResponse(`${suite_id}_${run_id}_scenario_results.csv`, columns, rows);
	}

	throw error(400, `Unknown export type: ${type}. Use results or scenario_results.`);
};
