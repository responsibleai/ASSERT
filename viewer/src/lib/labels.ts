import {
	POLICY_VIOLATION_NOT_PERMISSIBLE,
	POLICY_VIOLATION_PERMISSIBLE
} from './permissibility.js';

const METRIC_LABELS: Record<string, string> = {
	[POLICY_VIOLATION_NOT_PERMISSIBLE]: 'impermissible behavior violated',
	[POLICY_VIOLATION_PERMISSIBLE]: 'permissible behavior violated'
};

/**
 * Display label for a judge dimension. Falls back to the de-underscored metric key,
 * which is what every surface rendered before named labels existed.
 */
export function metricDisplayLabel(metric: string): string {
	return METRIC_LABELS[metric] ?? metric.replace(/_/g, ' ');
}

/**
 * Canonical heading form of a metric label. Only the first character is upper-cased
 * so inner casing and punctuation in a label survive.
 */
export function metricTitleLabel(metric: string): string {
	const label = metricDisplayLabel(metric);
	return label.charAt(0).toUpperCase() + label.slice(1);
}
