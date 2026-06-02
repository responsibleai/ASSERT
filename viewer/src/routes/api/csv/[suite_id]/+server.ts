// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { error } from '@sveltejs/kit';
import {
	loadPolicy,
	loadPromptSeeds,
	loadScenarioSeeds,
	listRuns,
	listAuditRuns,
	loadJudgedPrompts,
	loadAuditScores,
	loadAuditTranscripts
} from '$lib/server/data.js';
import { csvResponse } from '$lib/server/csv.js';
import type { RequestHandler } from './$types.js';
import type {
	AuditScore,
	AuditTranscript,
	InteractionMessage,
	JudgedSample,
	PromptSeed,
	ScenarioSeed
} from '$lib/types.js';

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
	const { suite_id } = params;
	if (!SAFE_ID.test(suite_id)) throw error(400, 'Invalid suite ID');
	const type = url.searchParams.get('type') ?? 'test_set';

	if (type === 'taxonomy') {
		const taxonomy = loadPolicy(suite_id);
		if (!taxonomy) throw error(404, 'Taxonomy not found');

		const columns = ['behavior', 'definition', 'permissible', 'examples'];
		const rows = taxonomy.behavior_categories.map((behavior) => ({
			behavior: behavior.name,
			definition: behavior.definition,
			permissible: behavior.permissible,
			examples: behavior.examples.join(' | ')
		}));
		return csvResponse(`${suite_id}_taxonomy.csv`, columns, rows);
	}

	if (type === 'test_set') {
		const testCases = loadPromptSeeds(suite_id);
		if (testCases.length === 0) throw error(404, 'No prompt test cases found');

		const variationNames = detectVariations(testCases);
		const columns = [
			'test_case_id',
			'behavior',
			'definition',
			'title',
			'description',
			'system_prompt',
			'tools',
			...variationNames
		];
		const rows = testCases.map((testCase: PromptSeed) => ({
			test_case_id: testCase.test_case_id,
			behavior: testCase.behavior,
			definition: testCase.definition,
			title: testCase.seed.title,
			description: testCase.seed.description,
			system_prompt: testCase.seed.system_prompt ?? '',
			tools: JSON.stringify(testCase.seed.tools ?? []),
			...Object.fromEntries(variationNames.map((name) => [name, testCase.dimensions?.[name] ?? '']))
		}));
		return csvResponse(`${suite_id}_test_set.csv`, columns, rows);
	}

	if (type === 'scenario_test_set') {
		const testCases = loadScenarioSeeds(suite_id);
		if (testCases.length === 0) throw error(404, 'No scenario test cases found');

		const variationNames = detectVariations(testCases);
		const columns = ['test_case_id', 'behavior', 'definition', 'title', 'description', 'tools', ...variationNames];
		const rows = testCases.map((testCase: ScenarioSeed) => ({
			test_case_id: testCase.test_case_id,
			behavior: testCase.behavior,
			definition: testCase.definition,
			title: testCase.seed.title,
			description: testCase.seed.description,
			tools: JSON.stringify(testCase.seed.tools ?? []),
			...Object.fromEntries(variationNames.map((name) => [name, testCase.dimensions?.[name] ?? '']))
		}));
		return csvResponse(`${suite_id}_scenario_test_set.csv`, columns, rows);
	}

	if (type === 'all_results') {
		const runs = listRuns(suite_id);
		const allSamples: Record<string, unknown>[] = [];
		const allScoreDimensionNames = new Set<string>();
		const allVariationNames = new Set<string>();

		for (const run of runs) {
			if (!run.has_judged) continue;
			const samples = loadJudgedPrompts(suite_id, run.run_id);
			for (const dimension of detectScoreDimensions(samples.map((sample) => sample.verdict as Record<string, unknown>))) {
				allScoreDimensionNames.add(dimension);
			}
			for (const variation of detectVariations(samples)) allVariationNames.add(variation);
			for (const sample of samples) {
				allSamples.push({
					run_id: run.run_id,
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
					...Object.fromEntries([...allVariationNames].map((name) => [name, sample.dimensions?.[name] ?? ''])),
					...Object.fromEntries(
						[...allScoreDimensionNames].map((dimension) => [
							dimension,
							dimensionValue(sample.verdict as Record<string, unknown> | null | undefined, dimension)
						])
					)
				});
			}
		}
		if (allSamples.length === 0) throw error(404, 'No results found across runs');

		const scoreDimensions = [...allScoreDimensionNames].sort();
		const variations = [...allVariationNames].sort();
		const columns = [
			'run_id',
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
		return csvResponse(`${suite_id}_all_results.csv`, columns, allSamples);
	}

	if (type === 'all_scenario_results') {
		const runs = listAuditRuns(suite_id);
		const allScores: Record<string, unknown>[] = [];
		const allScoreDimensionNames = new Set<string>();
		const allVariationNames = new Set<string>();

		for (const run of runs) {
			if (!run.has_scores) continue;
			const scores = loadAuditScores(suite_id, run.run_id);
			const transcripts = loadAuditTranscripts(suite_id, run.run_id);
			const transcriptMap = new Map<string, AuditTranscript>();
			for (const transcript of transcripts) transcriptMap.set(transcript.test_case_id, transcript);

			for (const dimension of detectScoreDimensions(scores.map((score) => score.verdict as Record<string, unknown>))) {
				allScoreDimensionNames.add(dimension);
			}
			for (const variation of detectVariations(scores)) allVariationNames.add(variation);
			for (const score of scores) {
				allScores.push({
					run_id: run.run_id,
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
					...Object.fromEntries([...allVariationNames].map((name) => [name, score.dimensions?.[name] ?? ''])),
					...Object.fromEntries(
						[...allScoreDimensionNames].map((dimension) => [
							dimension,
							dimensionValue(score.verdict as Record<string, unknown> | null | undefined, dimension)
						])
					)
				});
			}
		}
		if (allScores.length === 0) throw error(404, 'No scenario results found across runs');

		const scoreDimensions = [...allScoreDimensionNames].sort();
		const variations = [...allVariationNames].sort();
		const columns = [
			'run_id',
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
		return csvResponse(`${suite_id}_all_scenario_results.csv`, columns, allScores);
	}

	throw error(
		400,
		`Unknown export type: ${type}. Use taxonomy, test_set, scenario_test_set, all_results, or all_scenario_results.`
	);
};
