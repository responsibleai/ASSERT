// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/**
 * Shared helpers for dimension filtering across test_set and results pages.
 */

type FactorRecord = { dimensions?: Record<string, string> };

export function observedFactorNames(items: FactorRecord[]): string[] {
	const names = new Set<string>();
	for (const item of items) {
		for (const name of Object.keys(item.dimensions ?? {})) names.add(name);
	}
	return [...names].sort();
}

export function observedFactorLevels(items: FactorRecord[], factorName: string): string[] {
	const levels = new Set<string>();
	for (const item of items) {
		const v = item.dimensions?.[factorName];
		if (v) levels.add(v);
	}
	return [...levels].sort();
}

export function matchesFactorFilters(item: FactorRecord, filters: Record<string, string>): boolean {
	for (const [name, level] of Object.entries(filters)) {
		if (level && item.dimensions?.[name] !== level) return false;
	}
	return true;
}

export function hasActiveFilters(filters: Record<string, string>): boolean {
	return Object.values(filters).some(v => !!v);
}
