<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import type { TokenUsageView } from '$lib/types.js';
	import {
		actualIsWithinEstimate,
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
	let providerUsageIncomplete = $derived(
		!!actual && (actual.missingUsageCalls > 0 || actual.calls < actual.requests)
	);
	let actualLabel = $derived(providerUsageIncomplete ? 'Reported' : 'Actual');
	let actualUnavailableMessage = $derived(
		accuracy?.status === 'unavailable'
			? tokenAccuracyUnavailableMessage(accuracy.reason, accuracy.usageCoverage)
			: providerUsageIncomplete
				? tokenAccuracyUnavailableMessage('provider_usage_incomplete', actual?.usageCoverage ?? null)
				: null
	);
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

<section class="mb-4 rounded-md border border-border bg-surface px-3 py-2" aria-labelledby="token-usage-heading">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<div class="flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1 text-sm">
			<h2 id="token-usage-heading" class="text-sm font-semibold text-text">Token usage</h2>
			{#if estimate}
				<span class="text-text-secondary">
					Estimated
					<strong class="font-semibold tabular-nums text-text" title={exactTokenTitle(estimate.totalTokens)}>~{formatTokenCount(estimate.totalTokens)}</strong>
					<span class="text-xs text-text-muted">({formatTokenCount(estimate.lowerBoundTokens)}–{formatTokenCount(estimate.upperBoundTokens)})</span>
				</span>
			{:else}
				<span class="text-xs text-text-muted">Estimate unavailable</span>
			{/if}
			{#if actual && hasReportedActual}
				<span class="text-text-secondary">
					{actualLabel}
					<strong class="font-semibold tabular-nums text-text" title={exactTokenTitle(actual.totalTokens)}>{formatTokenCount(actual.totalTokens)}</strong>
				</span>
			{:else}
				<span class="text-xs text-text-muted">Actual unavailable</span>
			{/if}
			{#if accuracy?.status === 'available'}
				<span class="font-medium tabular-nums text-text">{formatActualVsEstimate(accuracy.differenceRatio)}</span>
			{/if}
		</div>
		{#if accuracy?.status === 'available' && withinRange !== null}
			<span class="inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium {withinRange ? 'bg-score-pass/10 text-score-pass' : 'bg-score-border/10 text-score-border'}">
				{withinRange ? 'In range' : 'Outside range'}
			</span>
		{/if}
	</div>

	{#if actual && hasReportedActual}
		<div class="mt-1 text-xs text-text-muted">
			{actual.calls}/{actual.requests || actual.calls} calls · {formatTokenCount(actual.inputTokens)} input / {formatTokenCount(actual.outputTokens)} output
			{#if actual.inputTokens > 0}
				· {formatTokenPercent(actual.cacheHitRate)} cached input
			{/if}
		</div>
		{#if actualUnavailableMessage}
			<div class="mt-1 text-xs text-text-muted">
				{actualUnavailableMessage}
			</div>
		{/if}
	{:else if accuracy?.status === 'unavailable'}
		<div class="mt-1 text-xs text-text-muted">
			{tokenAccuracyUnavailableMessage(accuracy.reason, accuracy.usageCoverage)}
		</div>
	{:else if actual}
		<div class="mt-1 text-xs text-text-muted">{actual.calls}/{actual.requests || actual.calls} calls reported complete usage.</div>
	{:else}
		<div class="mt-1 text-xs text-text-muted">No provider token usage was recorded.</div>
	{/if}

	{#if stageEstimates.length > 0 || estimate?.notes.length}
		<details class="mt-2 border-t border-border/60 pt-2 text-xs text-text-muted">
			<summary class="w-fit cursor-pointer select-none font-medium text-text-secondary">Details</summary>
			{#if stageEstimates.length > 0}
				<div class="mt-2 flex flex-wrap gap-2">
					{#each stageEstimates as [stage, stageEstimate]}
						<span class="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2 py-0.5 text-xs text-text-secondary">
							<span>{tokenStageLabel(stage)}</span>
							<span class="font-semibold tabular-nums text-text">{formatTokenCount(stageEstimate.totalTokens)}</span>
						</span>
					{/each}
				</div>
			{/if}
			{#if estimate?.notes.length}
				<ul class="mt-2 list-disc space-y-1 pl-4 text-[11px]">
					{#each estimate.notes as note}
						<li>{note}</li>
					{/each}
				</ul>
			{/if}
		</details>
	{/if}
</section>
