// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/**
 * Generic grouping system for scored results.
 * New grouping axes can be added by pushing to GROUP_AXES.
 */

import { getVerdictFlag, scoreSortValue } from './judgment.js';
import type { AuditScore, JudgedSample, GroupAxis, GroupContext, GroupEntry, NodeJudgment } from './types.js';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const NO_MATCHING_BEHAVIOR = 'No matching taxonomy behavior';
const JUDGE_FAILED = 'Judge failed to score';
const NOT_JUDGED = 'Not judged yet';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hasNodeJudgments(verdict: Record<string, unknown> | null | undefined): boolean {
	if (!verdict || typeof verdict !== 'object') return false;
	return Array.isArray(verdict.node_judgments);
}

function isErrorVerdict(verdict: Record<string, unknown>): boolean {
	return typeof verdict.error === 'string' && !!verdict.error;
}

function getRelevantNodeLabels(verdict: Record<string, unknown> | null | undefined, fallbackBehavior?: string): string[] {
	if (!verdict || typeof verdict !== 'object') return [];
	const nodeJudgments = verdict.node_judgments;
	if (!Array.isArray(nodeJudgments)) return [];
	const seen = new Set<string>();
	const labels: string[] = [];
	for (const node of nodeJudgments as NodeJudgment[]) {
		if (!node.relevant) continue;
		const name = (node.node_name ?? '').trim();
		const label = name || fallbackBehavior || 'Unmapped behavior category';
		if (seen.has(label)) continue;
		seen.add(label);
		labels.push(label);
	}
	return labels;
}

// ---------------------------------------------------------------------------
// Shared accessor/sort functions
// ---------------------------------------------------------------------------

type ScoredRecord = {
	behavior: string;
	permissible?: boolean | null;
	verdict?: Record<string, unknown> | null;
	judge_status?: string | null;
	judge_error?: string | null;
	dimensions?: Record<string, string>;
};

export function formatFactorLabel(name: string): string {
	return name
		.split('_')
		.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
		.join(' ');
}

function observedNodeAccessor(s: ScoredRecord): string | string[] {
	if (s.judge_status === 'judge_failed' || s.judge_error) return JUDGE_FAILED;
	if (!s.verdict) return NOT_JUDGED;
	if (isErrorVerdict(s.verdict)) return JUDGE_FAILED;
	if (!hasNodeJudgments(s.verdict)) return JUDGE_FAILED;
	const labels = getRelevantNodeLabels(s.verdict, s.behavior);
	if (labels.length === 0) return NO_MATCHING_BEHAVIOR;
	return labels;
}

function observedNodeSort(a: GroupEntry<ScoredRecord>, b: GroupEntry<ScoredRecord>): number {
	const specialOrder = (key: string) =>
		key === JUDGE_FAILED ? 3 : key === NOT_JUDGED ? 2 : key === NO_MATCHING_BEHAVIOR ? 1 : 0;
	const aSpecial = specialOrder(a.key);
	const bSpecial = specialOrder(b.key);
	if (aSpecial !== bSpecial) return aSpecial - bSpecial;
	return a.key.localeCompare(b.key);
}

// ---------------------------------------------------------------------------
// Axis registry (shared by prompt and audit views)
// ---------------------------------------------------------------------------

const GROUP_AXES: GroupAxis<ScoredRecord>[] = [
	{ key: 'observed_node', label: 'Behavior category', accessor: observedNodeAccessor, sortGroups: observedNodeSort },
];

export const AUDIT_GROUP_AXES: GroupAxis<AuditScore>[] = GROUP_AXES;
export const PROMPT_GROUP_AXES: GroupAxis<JudgedSample>[] = GROUP_AXES;

export function buildFactorAxes<T extends { dimensions?: Record<string, string> }>(
	items: T[]
): GroupAxis<T>[] {
	const factorNames = new Set<string>();
	for (const item of items) {
		for (const name of Object.keys(item.dimensions ?? {})) {
			factorNames.add(name);
		}
	}

	const orderedFactorNames = [...factorNames].sort((a, b) => a.localeCompare(b));
	const axes: GroupAxis<T>[] = orderedFactorNames.map((name) => ({
		key: `dimension:${name}`,
		label: formatFactorLabel(name),
		accessor: (item) => item.dimensions?.[name]
	}));

	return axes;
}

// ---------------------------------------------------------------------------
// Generic group-by function
// ---------------------------------------------------------------------------

export function groupByAxis<T extends { verdict?: Record<string, unknown> | null }>(
	items: T[],
	axis: GroupAxis<T>,
	metricNames: string[],
	context?: GroupContext,
): GroupEntry<T>[] {
	const map = new Map<string, { items: T[]; sums: Record<string, number>; counts: Record<string, number> }>();

	function addToBucket(key: string, item: T): void {
		if (!map.has(key)) {
			map.set(key, { items: [], sums: {}, counts: {} });
		}
		const bucket = map.get(key)!;
		bucket.items.push(item);

		for (const m of metricNames) {
			const flag = getVerdictFlag(item.verdict, m);
			if (flag !== null) {
				bucket.sums[m] = (bucket.sums[m] ?? 0) + Number(flag);
				bucket.counts[m] = (bucket.counts[m] ?? 0) + 1;
			}
		}

		// Per-behavior violation: check if the node matching this group key was violated
		const nodeJudgments = item.verdict?.node_judgments;
		if (Array.isArray(nodeJudgments)) {
			for (const node of nodeJudgments as NodeJudgment[]) {
				const name = (node.node_name ?? '').trim();
				const label = name || (item as unknown as ScoredRecord).behavior || 'Unmapped behavior category';
				if (label === key && node.relevant) {
					bucket.sums['behavior_violation'] = (bucket.sums['behavior_violation'] ?? 0) + (node.violated ? 1 : 0);
					bucket.counts['behavior_violation'] = (bucket.counts['behavior_violation'] ?? 0) + 1;
					break;
				}
			}
		}
	}

	for (const item of items) {
		const result = axis.accessor(item, context);
		if (Array.isArray(result)) {
			if (result.length === 0) {
				addToBucket('(ungrouped)', item);
			} else {
				for (const key of result) {
					addToBucket(key ?? '(ungrouped)', item);
				}
			}
		} else {
			addToBucket(result ?? '(ungrouped)', item);
		}
	}

	const entries: GroupEntry<T>[] = [...map.entries()].map(([key, g]) => {
		const avgs: Record<string, number> = {};
		for (const m of metricNames) {
			if (g.counts[m]) avgs[m] = g.sums[m] / g.counts[m];
		}
		if (g.counts['behavior_violation']) {
			avgs['behavior_violation'] = g.sums['behavior_violation'] / g.counts['behavior_violation'];
		}
		return {
			key,
			label: key,
			items: [...g.items].sort(
				(a, b) => {
					return scoreSortValue(a as { verdict?: Record<string, unknown> | null }, 'policy_violation')
						- scoreSortValue(b as { verdict?: Record<string, unknown> | null }, 'policy_violation');
				}
			),
			avgs,
			total: g.items.length,
		};
	});

	if (axis.sortGroups) {
		entries.sort(axis.sortGroups);
	} else {
		entries.sort((a, b) => a.key.localeCompare(b.key));
	}

	return entries;
}
