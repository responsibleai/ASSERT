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
import type { PromptSeed, ScenarioSeed, AuditTranscript } from '$lib/types.js';

const SAFE_ID = /^[\w.@()-]+$/;

export const GET: RequestHandler = async ({ params, url }) => {
	const { suite_id } = params;
	if (!SAFE_ID.test(suite_id)) throw error(400, 'Invalid suite ID');
	const type = url.searchParams.get('type') ?? 'test_set';

	if (type === 'taxonomy') {
		const taxonomy = loadPolicy(suite_id);
		if (!taxonomy) throw error(404, 'Taxonomy not found');

		const columns = ['behavior_category', 'definition', 'permissible', 'examples'];
		const rows = taxonomy.behavior_categories.map((behavior) => ({
			behavior_category: behavior.name,
			definition: behavior.definition,
			permissible: behavior.permissible,
			examples: (behavior.examples ?? []).join(' | ')
		}));
		return csvResponse(`${suite_id}_taxonomy.csv`, columns, rows);
	}

	if (type === 'test_set') {
		const seeds = loadPromptSeeds(suite_id);
		if (seeds.length === 0) throw error(404, 'No test cases found');

		const dimensions = detectStratificationDimensions(seeds);
		const columns = ['test_case_id', 'behavior', 'definition', 'title', 'description', 'system_prompt', 'tools', ...dimensions];
		const rows = seeds.map((s: PromptSeed) => ({
			test_case_id: s.test_case_id,
			behavior: s.behavior,
			definition: s.definition,
			title: s.seed.title,
			description: s.seed.description,
			system_prompt: s.seed.system_prompt ?? '',
			tools: JSON.stringify(s.seed.tools ?? []),
			...Object.fromEntries(dimensions.map((dimension) => [dimension, s.dimensions?.[dimension] ?? '']))
		}));
		return csvResponse(`${suite_id}_test_set.csv`, columns, rows);
	}

	if (type === 'scenario_test_set') {
		const scenarioSeeds = loadScenarioSeeds(suite_id);
		if (scenarioSeeds.length === 0) throw error(404, 'No scenario test cases found');

		const dimensions = detectStratificationDimensions(scenarioSeeds);
		const columns = ['test_case_id', 'behavior', 'definition', 'title', 'description', 'tools', ...dimensions];
		const rows = scenarioSeeds.map((s: ScenarioSeed) => ({
			test_case_id: s.test_case_id,
			behavior: s.behavior,
			definition: s.definition,
			title: s.seed.title,
			description: s.seed.description,
			tools: JSON.stringify(s.seed.tools ?? []),
			...Object.fromEntries(dimensions.map((dimension) => [dimension, s.dimensions?.[dimension] ?? '']))
		}));
		return csvResponse(`${suite_id}_scenario_test_set.csv`, columns, rows);
	}

	if (type === 'all_results') {
		const runs = listRuns(suite_id);
		const allSamples: Record<string, unknown>[] = [];
		const allJudgeDims = new Set<string>();
		const allDimensions = new Set<string>();

		for (const run of runs) {
			if (!run.has_judged) continue;
			const samples = loadJudgedPrompts(suite_id, run.run_id);
			for (const dim of detectJudgeDimensions(samples.map((s) => s.verdict as Record<string, unknown>))) {
				allJudgeDims.add(dim);
			}
			for (const dimension of detectStratificationDimensions(samples)) allDimensions.add(dimension);
			for (const s of samples) {
				allSamples.push({
					run_id: run.run_id,
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
					...Object.fromEntries([...allDimensions].map((dimension) => [dimension, s.dimensions?.[dimension] ?? ''])),
					...Object.fromEntries([...allJudgeDims].map((dim) => [dim, judgeDimensionValue(s.verdict as Record<string, unknown> | null | undefined, dim)]))
				});
			}
		}
		if (allSamples.length === 0) throw error(404, 'No results found across runs');

		const judgeDims = [...allJudgeDims].sort();
		const dimensions = [...allDimensions].sort();
		const columns = ['run_id', 'test_case_id', 'prompt', 'response', 'conversation', 'behavior', 'target', 'target_runtime_mode', 'judge_model', 'judge_status', 'judge_error', 'justification', ...dimensions, ...judgeDims];
		return csvResponse(`${suite_id}_all_results.csv`, columns, allSamples);
	}

	if (type === 'all_audit_scores') {
		const runs = listAuditRuns(suite_id);
		const allScores: Record<string, unknown>[] = [];
		const allJudgeDims = new Set<string>();
		const allDimensions = new Set<string>();

		for (const run of runs) {
			if (!run.has_scores) continue;
			const scores = loadAuditScores(suite_id, run.run_id);
			const transcripts = loadAuditTranscripts(suite_id, run.run_id);
			const transcriptMap = new Map<string, AuditTranscript>();
			for (const transcript of transcripts) transcriptMap.set(transcript.test_case_id, transcript);

			for (const dim of detectJudgeDimensions(scores.map((s) => s.verdict as Record<string, unknown>))) {
				allJudgeDims.add(dim);
			}
			for (const dimension of detectStratificationDimensions(scores)) allDimensions.add(dimension);
			for (const s of scores) {
				allScores.push({
					run_id: run.run_id,
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
					...Object.fromEntries([...allDimensions].map((dimension) => [dimension, s.dimensions?.[dimension] ?? ''])),
					...Object.fromEntries([...allJudgeDims].map((dim) => [dim, judgeDimensionValue(s.verdict as Record<string, unknown> | null | undefined, dim)]))
				});
			}
		}
		if (allScores.length === 0) throw error(404, 'No inference scores found across runs');

		const judgeDims = [...allJudgeDims].sort();
		const dimensions = [...allDimensions].sort();
		const columns = ['run_id', 'test_case_id', 'behavior', 'target', 'tester_model', 'target_runtime_mode', 'judge_model', 'judge_status', 'judge_error', 'justification', 'conversation', 'turns_count', 'stop_reason', ...dimensions, ...judgeDims];
		return csvResponse(`${suite_id}_all_inference_scores.csv`, columns, allScores);
	}

	throw error(400, `Unknown export type: ${type}. Use taxonomy, test_set, scenario_test_set, all_results, or all_audit_scores.`);
};
