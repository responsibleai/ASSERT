<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import type { PreRunTokenEstimate } from '$lib/types.js';
	import { formatTokenCount, splitTokenEstimateNotes } from '$lib/token-usage.js';

	let {
		estimate,
		loading = false,
		error = ''
	}: {
		estimate: PreRunTokenEstimate | null;
		loading?: boolean;
		error?: string;
	} = $props();
	let notes = $derived(splitTokenEstimateNotes(estimate?.notes));
</script>

<div class="mb-5 flex min-h-14 flex-wrap items-center justify-between gap-3 rounded-md border border-border bg-bg px-4 py-2.5" aria-live="polite">
	<div>
		<div class="text-xs font-semibold text-text">Estimated token usage</div>
		<div class="mt-0.5 text-[11px] text-text-muted">Conservative local estimate; no provider call.</div>
	</div>
	{#if loading}
		<div class="flex items-center gap-2 text-xs text-text-muted">
			<svg class="h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
			Estimating…
		</div>
	{:else if estimate}
		<div class="flex flex-wrap items-baseline justify-end gap-x-2 gap-y-0.5 text-right">
			{#if estimate.total_tokens === 0 && notes.caveats.length > 0}
				<span class="text-xs text-text-secondary">0 known tokens · total unknown</span>
			{:else}
				<span class="text-base font-semibold tabular-nums text-text" title={`${estimate.total_tokens.toLocaleString('en-US')} tokens`}>~{formatTokenCount(estimate.total_tokens)}</span>
				<span class="text-xs text-text-muted">Range {formatTokenCount(estimate.lower_bound_tokens)}–{formatTokenCount(estimate.upper_bound_tokens)} · {estimate.calls} {estimate.calls === 1 ? 'call' : 'calls'}{notes.caveats.length > 0 ? ' · partial estimate' : ''}</span>
			{/if}
		</div>
	{:else if error}
		<span class="text-xs text-score-fail" title={error}>Estimate unavailable</span>
	{:else}
		<span class="text-xs text-text-muted">Complete required fields to estimate</span>
	{/if}
	{#if !loading && estimate}
		{#if notes.caveats.length > 0}
			<ul class="w-full space-y-1 text-xs text-text-secondary">
				{#each notes.caveats as note}
					<li>{note}</li>
				{/each}
			</ul>
		{/if}
		{#if notes.details.length > 0}
			<details class="w-full text-xs text-text-muted">
				<summary class="w-fit cursor-pointer select-none">Details</summary>
				<ul class="mt-1 list-disc space-y-1 pl-4 text-[11px]">
					{#each notes.details as note}
						<li>{note}</li>
					{/each}
				</ul>
			</details>
		{/if}
	{/if}
</div>
