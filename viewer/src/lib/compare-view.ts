// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { getRecordFlag, getVerdictFlag } from '$lib/judgment.js';
import type { JudgedSample } from '$lib/types.js';

export interface MatchedSampleRow {
	prompt: string;
	samples: Record<string, JudgedSample | null>;
}

export function buildMatchedSampleRows(
	samplesByRunId: Record<string, JudgedSample[]>,
	runIds: string[],
	metric: string,
	disagreementsOnly: boolean,
	baselineRunId?: string
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

	// Three-key sort to lead with the regression-fix narrative without hiding regressions:
	//   1. baseline-flagged DESC (baseline-fail rows first — the "wins" demo case)
	//   2. has-disagreement DESC (mixed-outcome rows next)
	//   3. |delta| on the active metric DESC (largest spread first)
	//   tiebreak: prompt for stable, reproducible ordering across reloads
	const flagOf = (row: MatchedSampleRow, runId: string | undefined): boolean | null => {
		if (!runId) return null;
		const sample = row.samples[runId];
		return sample ? getRecordFlag(sample, metric) : null;
	};
	const absDelta = (row: MatchedSampleRow): number => {
		const scores: number[] = [];
		for (const sample of Object.values(row.samples)) {
			if (!sample) continue;
			const flag = getRecordFlag(sample, metric);
			if (flag === true) scores.push(1);
			else if (flag === false) scores.push(0);
		}
		if (scores.length < 2) return 0;
		return Math.max(...scores) - Math.min(...scores);
	};
	const disagrees = (row: MatchedSampleRow): boolean => {
		const scores = Object.values(row.samples)
			.filter(Boolean)
			.map((sample) => getRecordFlag(sample!, metric))
			.filter((s): s is boolean => s !== null);
		return new Set(scores).size > 1;
	};

	rows.sort((left, right) => {
		// Key 1: baseline-flagged (only when baselineRunId provided)
		if (baselineRunId) {
			const lFlagged = flagOf(left, baselineRunId) === true ? 1 : 0;
			const rFlagged = flagOf(right, baselineRunId) === true ? 1 : 0;
			if (lFlagged !== rFlagged) return rFlagged - lFlagged;
		}
		// Key 2: disagreement
		const lDisagrees = disagrees(left) ? 1 : 0;
		const rDisagrees = disagrees(right) ? 1 : 0;
		if (lDisagrees !== rDisagrees) return rDisagrees - lDisagrees;
		// Key 3: |delta|
		const lDelta = absDelta(left);
		const rDelta = absDelta(right);
		if (lDelta !== rDelta) return rDelta - lDelta;
		// Tiebreak: prompt for reproducibility
		return left.prompt.localeCompare(right.prompt);
	});

	return rows;
}
