/**
 * Shared helpers for factor filtering across seeds and results pages.
 */

type FactorRecord = { factors?: Record<string, string> };

export function observedFactorNames(items: FactorRecord[]): string[] {
	const names = new Set<string>();
	for (const item of items) {
		for (const name of Object.keys(item.factors ?? {})) names.add(name);
	}
	return [...names].sort();
}

export function observedFactorLevels(items: FactorRecord[], factorName: string): string[] {
	const levels = new Set<string>();
	for (const item of items) {
		const v = item.factors?.[factorName];
		if (v) levels.add(v);
	}
	return [...levels].sort();
}

export function matchesFactorFilters(item: FactorRecord, filters: Record<string, string>): boolean {
	for (const [name, level] of Object.entries(filters)) {
		if (level && item.factors?.[name] !== level) return false;
	}
	return true;
}

export function hasActiveFilters(filters: Record<string, string>): boolean {
	return Object.values(filters).some(v => !!v);
}
