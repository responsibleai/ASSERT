export function judgeDimensionLabel(metric: string): string {
	if (metric === 'policy_violation') return 'Behavior violation';
	return metric.replace(/_/g, ' ');
}

export function titleCaseJudgeDimensionLabel(metric: string): string {
	const label = judgeDimensionLabel(metric);
	return label.charAt(0).toUpperCase() + label.slice(1).toLowerCase();
}
