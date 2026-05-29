// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { getRecordFlag, getVerdictFlag, scoreSortValue } from '$lib/judgment.js';
import type { JudgedSample } from '$lib/types.js';

export interface MatchedSampleRow {
	prompt: string;
	samples: Record<string, JudgedSample | null>;
}

export function buildMatchedSampleRows(
	samplesByRunId: Record<string, JudgedSample[]>,
	runIds: string[],
	metric: string,
	disagreementsOnly: boolean
): MatchedSampleRow[] {
	const promptMap = new Map<string, Record<string, JudgedSample | null>>();

	for (const runId of runIds) {
		for (const sample of samplesByRunId[runId] ?? []) {
			if (!promptMap.has(sample.prompt)) {
				promptMap.set(
					sample.prompt,
					Object.fromEntries(runIds.map((id) => [id, null])) as Record<string, JudgedSample | null>
				);
			}
			promptMap.get(sample.prompt)![runId] = sample;
		}
	}

	let rows = Array.from(promptMap.entries()).map(([prompt, samples]) => ({ prompt, samples }));
	if (disagreementsOnly) {
		rows = rows.filter((row) => {
			const scores = Object.values(row.samples)
				.filter(Boolean)
				.map((sample) => getVerdictFlag(sample!.verdict, metric));
			return new Set(scores).size > 1;
		});
	}

	rows.sort((left, right) => {
		const leftScores = Object.values(left.samples)
			.filter(Boolean)
			.map((sample) => getRecordFlag(sample!, metric))
			.filter((score): score is boolean => score !== null);
		const rightScores = Object.values(right.samples)
			.filter(Boolean)
			.map((sample) => getRecordFlag(sample!, metric))
			.filter((score): score is boolean => score !== null);
		const leftDisagrees = new Set(leftScores).size > 1 ? 0 : 1;
		const rightDisagrees = new Set(rightScores).size > 1 ? 0 : 1;
		if (leftDisagrees !== rightDisagrees) return leftDisagrees - rightDisagrees;

		const leftFirst = Object.values(left.samples).find(Boolean);
		const rightFirst = Object.values(right.samples).find(Boolean);
		return scoreSortValue(leftFirst ?? {}, metric) - scoreSortValue(rightFirst ?? {}, metric);
	});

	return rows;
}
