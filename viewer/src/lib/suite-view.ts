// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import type {
	AuditRunListItem,
	PromptSeed,
	RunListItem,
	ScenarioSeed,
	Behavior,
	ViewerSeedGroup,
	ViewerSeedItem
} from '$lib/types.js';

export interface CombinedRunEntry {
	run_id: string;
	compare_run_id: string | null;
	prompt_run_id: string | null;
	audit_run_id: string | null;
	prompt: RunListItem | null;
	audit: AuditRunListItem | null;
}

export function normalizePromptSeeds(items: PromptSeed[]): ViewerSeedItem[] {
	return items.map((seed) => ({
		id: seed.test_case_id,
		kind: 'prompt',
		title: seed.seed.title || seed.behavior,
		description: seed.seed.description,
		behavior: seed.behavior,
		definition: seed.definition,
		system_prompt: seed.seed.system_prompt ?? null,
		tools: seed.seed.tools,
		dimensions: seed.dimensions
	}));
}

export function normalizeScenarioSeeds(items: ScenarioSeed[]): ViewerSeedItem[] {
	return items.map((seed) => ({
		id: seed.test_case_id,
		kind: 'scenario',
		title: seed.seed.title,
		description: seed.seed.description,
		behavior: seed.behavior,
		definition: seed.definition,
		system_prompt: seed.seed.system_prompt ?? null,
		tools: seed.seed.tools,
		dimensions: seed.dimensions
	}));
}

export function filterViewerSeeds(
	items: ViewerSeedItem[],
	query: string
): ViewerSeedItem[] {
	if (!query) return items;
	const normalizedQuery = query.toLowerCase();
	return items.filter(
		(seed) =>
			seed.title.toLowerCase().includes(normalizedQuery) ||
			seed.description.toLowerCase().includes(normalizedQuery) ||
			seed.behavior.toLowerCase().includes(normalizedQuery)
	);
}

export function groupViewerSeedsByPolicy(
	items: ViewerSeedItem[],
	behavior_categories: Behavior[]
): ViewerSeedGroup[] {
	const groupedSeeds = new Map<string, ViewerSeedItem[]>();
	for (const seed of items) {
		if (!groupedSeeds.has(seed.behavior)) groupedSeeds.set(seed.behavior, []);
		groupedSeeds.get(seed.behavior)!.push(seed);
	}

	const orderedGroups: ViewerSeedGroup[] = [];
	for (const beh of behavior_categories) {
		const matchingSeeds = groupedSeeds.get(beh.name);
		if (!matchingSeeds) continue;
		orderedGroups.push({
			name: beh.name,
			permissible: beh.permissible,
			definition: beh.definition,
			items: matchingSeeds
		});
		groupedSeeds.delete(beh.name);
	}

	for (const [name, remainingSeeds] of groupedSeeds) {
		orderedGroups.push({
			name,
			definition: remainingSeeds[0]?.definition ?? '',
			items: remainingSeeds
		});
	}

	return orderedGroups;
}

export function groupSeedsByFactor(
	items: ViewerSeedItem[],
	factorName: string
): ViewerSeedGroup[] {
	const groups = new Map<string, ViewerSeedItem[]>();
	for (const item of items) {
		const level = item.dimensions?.[factorName] ?? '(none)';
		if (!groups.has(level)) groups.set(level, []);
		groups.get(level)!.push(item);
	}
	return [...groups.entries()]
		.sort(([a], [b]) => a.localeCompare(b))
		.map(([name, groupItems]) => ({ name, items: groupItems }));
}

export function groupSeedsByCrossFactors(
	items: ViewerSeedItem[],
	factorA: string,
	factorB: string
): ViewerSeedGroup[] {
	const groups = new Map<string, ViewerSeedItem[]>();
	for (const item of items) {
		const a = item.dimensions?.[factorA] ?? '(none)';
		const b = item.dimensions?.[factorB] ?? '(none)';
		const key = `${a} · ${b}`;
		if (!groups.has(key)) groups.set(key, []);
		groups.get(key)!.push(item);
	}
	return [...groups.entries()]
		.sort(([a], [b]) => a.localeCompare(b))
		.map(([name, groupItems]) => ({ name, items: groupItems }));
}

export function mergeRunLists(
	runs: RunListItem[],
	auditRuns: AuditRunListItem[]
): CombinedRunEntry[] {
	const combinedRuns = new Map<string, CombinedRunEntry>();

	for (const run of runs) {
		combinedRuns.set(run.run_id, {
			run_id: run.run_id,
			compare_run_id: run.run_id,
			prompt_run_id: run.run_id,
			audit_run_id: null,
			prompt: run,
			audit: null
		});
	}

	for (const auditRun of auditRuns) {
		const existing = combinedRuns.get(auditRun.run_id);
		if (existing) {
			existing.audit = auditRun;
			existing.audit_run_id = auditRun.run_id;
			continue;
		}

		combinedRuns.set(auditRun.run_id, {
			run_id: auditRun.run_id,
			compare_run_id: null,
			prompt_run_id: null,
			audit_run_id: auditRun.run_id,
			prompt: null,
			audit: auditRun
		});
	}

	return [...combinedRuns.values()];
}
