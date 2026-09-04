// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

type EstimateRange = {
	lowerBoundTokens: number;
	upperBoundTokens: number;
};

const compactTokenFormatter = new Intl.NumberFormat('en-US', {
	notation: 'compact',
	maximumFractionDigits: 1
});

const TOKEN_STAGE_LABELS: Record<string, string> = {
	systematize: 'Behavior categories',
	test_set: 'Test set',
	inference: 'Inference',
	judge: 'Scoring'
};

export function formatTokenCount(value: number): string {
	const rounded = Math.max(0, Math.round(value));
	return rounded < 1000 ? rounded.toLocaleString('en-US') : compactTokenFormatter.format(rounded);
}

export function formatTokenPercent(value: number): string {
	return `${(Math.max(0, value) * 100).toFixed(1)}%`;
}

export function formatActualVsEstimate(differenceRatio: number): string {
	if (Math.abs(differenceRatio) < 0.0005) return 'Matched estimate';
	return `${Math.abs(differenceRatio * 100).toFixed(1)}% ${differenceRatio > 0 ? 'higher' : 'lower'}`;
}

export function actualVsEstimateSentence(differenceRatio: number): string {
	if (Math.abs(differenceRatio) < 0.0005) return 'Actual usage matched the pre-run estimate.';
	return `Actual usage was ${differenceRatio > 0 ? 'above' : 'below'} the pre-run estimate.`;
}

export function actualIsWithinEstimate(
	actualTokens: number,
	estimate: EstimateRange
): boolean {
	return actualTokens >= estimate.lowerBoundTokens && actualTokens <= estimate.upperBoundTokens;
}

export function tokenAccuracyUnavailableMessage(
	reason: string,
	usageCoverage: number | null
): string {
	if (reason === 'pipeline_incomplete') return 'The pipeline did not complete.';
	if (reason === 'pipeline_partial') return 'The pipeline returned a partial result.';
	if (reason === 'no_usage_reported') return 'The provider did not report token usage.';
	if (reason === 'provider_usage_incomplete') {
		return usageCoverage === null
			? 'Some provider calls did not report complete usage.'
			: `Complete usage was reported for ${formatTokenPercent(usageCoverage)} of calls.`;
	}
	return 'A complete comparison is not available for this run.';
}

export function tokenStageLabel(stage: string): string {
	return TOKEN_STAGE_LABELS[stage] ?? stage.replace(/_/g, ' ');
}
