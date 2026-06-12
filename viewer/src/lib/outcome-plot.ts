// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { getRecordFlag } from './judgment.js';
import type { Behavior, NodeJudgment } from './types.js';

export type OutcomeKind = 'dimension' | 'behavior';

export interface SelectableOutcome {
	id: string;
	kind: OutcomeKind;
	key: string;
	label: string;
	groupLabel: string;
	denominatorLabel: string;
}

export interface OutcomePlotRow {
	factor: string;
	level: string;
	n: number;
	flagged: number;
	rate: number;
	ciLow: number;
	ciHigh: number;
}

export interface OutcomeRecord {
	verdict?: Record<string, unknown> | null;
	dimensions?: Record<string, string>;
}

function outcomeId(kind: OutcomeKind, key: string): string {
	return `${kind}:${encodeURIComponent(key)}`;
}

function readDimensionNames(items: OutcomeRecord[]): string[] {
	const names = new Set<string>();
	for (const item of items) {
		const dimensions = item.verdict?.dimensions;
		if (!dimensions || typeof dimensions !== 'object' || Array.isArray(dimensions)) continue;
		for (const [name, value] of Object.entries(dimensions)) {
			if (typeof value === 'boolean') names.add(name);
		}
	}
	return [...names].sort((left, right) => {
		if (left === 'policy_violation') return -1;
		if (right === 'policy_violation') return 1;
		return left.localeCompare(right);
	});
}

function readObservedBehaviorNames(items: OutcomeRecord[]): Set<string> {
	const names = new Set<string>();
	for (const item of items) {
		const nodeJudgments = item.verdict?.node_judgments;
		if (!Array.isArray(nodeJudgments)) continue;
		for (const node of nodeJudgments as NodeJudgment[]) {
			const name = node.node_name?.trim();
			if (name) names.add(name);
		}
	}
	return names;
}

export function buildOutcomeOptions(
	items: OutcomeRecord[],
	behaviorCategories: Behavior[] = []
): SelectableOutcome[] {
	const dimensionOptions = readDimensionNames(items).map((name) => ({
		id: outcomeId('dimension', name),
		kind: 'dimension' as const,
		key: name,
		label: name.replace(/_/g, ' '),
		groupLabel: 'Judge dimensions',
		denominatorLabel: 'scored'
	}));

	const observedBehaviorNames = readObservedBehaviorNames(items);
	const behaviorNames = new Set<string>();
	for (const behavior of behaviorCategories) behaviorNames.add(behavior.name);
	for (const name of [...observedBehaviorNames].sort((a, b) => a.localeCompare(b))) {
		behaviorNames.add(name);
	}

	const behaviorOptions = [...behaviorNames].map((name) => ({
		id: outcomeId('behavior', name),
		kind: 'behavior' as const,
		key: name,
		label: name,
		groupLabel: 'Behavior categories',
		denominatorLabel: 'assessed relevant'
	}));

	return [...dimensionOptions, ...behaviorOptions];
}

export function wilsonInterval(flagged: number, n: number): { low: number; high: number } {
	if (n <= 0) return { low: 0, high: 0 };
	const z = 1.959963984540054;
	const z2 = z * z;
	const p = flagged / n;
	const denom = 1 + z2 / n;
	const center = (p + z2 / (2 * n)) / denom;
	const half = (z * Math.sqrt((p * (1 - p) + z2 / (4 * n)) / n)) / denom;
	return { low: Math.max(0, center - half), high: Math.min(1, center + half) };
}

function behaviorFlag(item: OutcomeRecord, behaviorName: string): boolean | null {
	const nodeJudgments = item.verdict?.node_judgments;
	if (!Array.isArray(nodeJudgments)) return null;
	for (const node of nodeJudgments as NodeJudgment[]) {
		if (node.node_name !== behaviorName || node.relevant !== true) continue;
		return typeof node.violated === 'boolean' ? node.violated : null;
	}
	return null;
}

function outcomeFlag(item: OutcomeRecord, outcome: SelectableOutcome): boolean | null {
	if (outcome.kind === 'dimension') return getRecordFlag(item, outcome.key);
	return behaviorFlag(item, outcome.key);
}

function rateRow(factor: string, level: string, items: OutcomeRecord[], outcome: SelectableOutcome): OutcomePlotRow {
	let n = 0;
	let flagged = 0;
	for (const item of items) {
		const flag = outcomeFlag(item, outcome);
		if (flag === null) continue;
		n += 1;
		if (flag) flagged += 1;
	}
	const interval = wilsonInterval(flagged, n);
	return {
		factor,
		level,
		n,
		flagged,
		rate: n > 0 ? flagged / n : 0,
		ciLow: interval.low,
		ciHigh: interval.high
	};
}

export function buildOutcomePlotRows(
	items: OutcomeRecord[],
	outcome: SelectableOutcome
): OutcomePlotRow[] {
	const rows = [rateRow('Overall', 'All results', items, outcome)];
	const dimensionNames = new Set<string>();
	for (const item of items) {
		for (const name of Object.keys(item.dimensions ?? {})) dimensionNames.add(name);
	}

	for (const dimension of [...dimensionNames].sort((a, b) => a.localeCompare(b))) {
		const levels = new Set<string>();
		for (const item of items) {
			const level = item.dimensions?.[dimension];
			if (level) levels.add(level);
		}
		for (const level of [...levels].sort((a, b) => a.localeCompare(b))) {
			const row = rateRow(
				dimension,
				level,
				items.filter((item) => item.dimensions?.[dimension] === level),
				outcome
			);
			if (row.n > 0) rows.push(row);
		}
	}

	return rows;
}
