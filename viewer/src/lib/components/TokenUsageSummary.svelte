<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import type { TokenUsageView } from '$lib/types.js';
	import {
		actualIsWithinEstimate,
		actualVsEstimateSentence,
		formatActualVsEstimate,
		formatTokenCount,
		formatTokenPercent,
		tokenAccuracyUnavailableMessage,
		tokenStageLabel
	} from '$lib/token-usage.js';

	let { tokenUsage }: { tokenUsage: TokenUsageView } = $props();
	let estimate = $derived(tokenUsage.estimate);
	let actual = $derived(tokenUsage.actual);
	let accuracy = $derived(tokenUsage.accuracy);
	let hasReportedActual = $derived(!!actual && (actual.calls > 0 || actual.totalTokens > 0));
	let stageEstimates = $derived(
		Object.entries(estimate?.stages ?? {}).filter(([, stage]) => stage.totalTokens > 0)
	);
	let withinRange = $derived(
		estimate && actual ? actualIsWithinEstimate(actual.totalTokens, estimate) : null
	);

	function exactTokenTitle(value: number): string {
		return `${Math.max(0, Math.round(value)).toLocaleString('en-US')} tokens`;
	}
</script>

<section class="mb-6 rounded-lg border border-border bg-surface px-5 py-4" aria-labelledby="token-usage-heading">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div>
			<h2 id="token-usage-heading" class="text-base font-semibold text-text">Token usage</h2>
			<p class="mt-0.5 text-xs text-text-muted">Conservative local pre-run estimate compared with provider-reported usage.</p>
		</div>
		{#if accuracy?.status === 'available' && withinRange !== null}
			<span class="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium {withinRange ? 'bg-score-pass/10 text-score-pass' : 'bg-score-border/10 text-score-border'}">
				{withinRange ? 'Within estimated range' : 'Outside estimated range'}
			</span>
		{/if}
	</div>

	<div class="mt-4 grid gap-3 md:grid-cols-3">
		<div class="rounded-lg border border-border/70 bg-background px-4 py-3">
			<div class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">Estimated</div>
			{#if estimate}
				<div class="mt-1 text-2xl font-semibold tabular-nums text-text" title={exactTokenTitle(estimate.totalTokens)}>
					~{formatTokenCount(estimate.totalTokens)}
				</div>
				<div class="mt-1 text-xs text-text-muted">
					Likely {formatTokenCount(estimate.lowerBoundTokens)}&ndash;{formatTokenCount(estimate.upperBoundTokens)}
				</div>
				<div class="mt-2 text-[11px] text-text-muted">
					{formatTokenCount(estimate.inputTokens)} input / {formatTokenCount(estimate.outputTokens)} output across {estimate.calls} tracked {estimate.calls === 1 ? 'call' : 'calls'}
				</div>
			{:else}
				<div class="mt-1 text-2xl font-semibold text-text-muted">Unavailable</div>
				<div class="mt-2 text-[11px] text-text-muted">This run does not include a pre-run estimate.</div>
			{/if}
		</div>

		<div class="rounded-lg border border-border/70 bg-background px-4 py-3">
			<div class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">Actual</div>
			{#if actual && hasReportedActual}
				<div class="mt-1 text-2xl font-semibold tabular-nums text-text" title={exactTokenTitle(actual.totalTokens)}>
					{formatTokenCount(actual.totalTokens)}
				</div>
				<div class="mt-1 text-xs text-text-muted">
					{formatTokenCount(actual.inputTokens)} input / {formatTokenCount(actual.outputTokens)} output
				</div>
				<div class="mt-2 text-[11px] text-text-muted">
					{actual.calls}/{actual.requests || actual.calls} calls reported
					{#if actual.inputTokens > 0}
						&nbsp;/&nbsp;{formatTokenPercent(actual.cacheHitRate)} cached input
					{/if}
				</div>
			{:else if actual}
				<div class="mt-1 text-2xl font-semibold text-text-muted">Unavailable</div>
				<div class="mt-2 text-[11px] text-text-muted">
					{actual.calls}/{actual.requests || actual.calls} calls reported complete usage.
				</div>
			{:else}
				<div class="mt-1 text-2xl font-semibold text-text-muted">Unavailable</div>
				<div class="mt-2 text-[11px] text-text-muted">No provider token usage was recorded.</div>
			{/if}
		</div>

		<div class="rounded-lg border border-border/70 bg-background px-4 py-3">
			<div class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">Actual vs estimate</div>
			{#if accuracy?.status === 'available'}
				<div class="mt-1 text-2xl font-semibold tabular-nums text-text">
					{formatActualVsEstimate(accuracy.differenceRatio)}
				</div>
				<div class="mt-1 text-xs text-text-muted">
					{actualVsEstimateSentence(accuracy.differenceRatio)}
				</div>
				<div class="mt-2 text-[11px] text-text-muted">
					Absolute error: {formatTokenPercent(accuracy.absolutePercentageError)}
				</div>
			{:else if accuracy?.status === 'unavailable'}
				<div class="mt-1 text-2xl font-semibold text-text-muted">Unavailable</div>
				<div class="mt-2 text-[11px] text-text-muted">
					{tokenAccuracyUnavailableMessage(accuracy.reason, accuracy.usageCoverage)}
				</div>
			{:else}
				<div class="mt-1 text-2xl font-semibold text-text-muted">Unavailable</div>
				<div class="mt-2 text-[11px] text-text-muted">This run does not include a complete comparison.</div>
			{/if}
		</div>
	</div>

	{#if stageEstimates.length > 0}
		<div class="mt-4 border-t border-border/60 pt-3">
			<div class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">Estimated by stage</div>
			<div class="mt-2 flex flex-wrap gap-2">
				{#each stageEstimates as [stage, stageEstimate]}
					<span class="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-xs text-text-secondary">
						<span>{tokenStageLabel(stage)}</span>
						<span class="font-semibold tabular-nums text-text">{formatTokenCount(stageEstimate.totalTokens)}</span>
					</span>
				{/each}
			</div>
		</div>
	{/if}

	{#if estimate?.notes.length}
		<div class="mt-3 border-t border-border/60 pt-3 text-[11px] text-text-muted">
			<div class="font-semibold uppercase tracking-wider">Estimator notes</div>
			<ul class="mt-1.5 list-disc space-y-1 pl-4">
				{#each estimate.notes as note}
					<li>{note}</li>
				{/each}
			</ul>
		</div>
	{/if}
</section>
