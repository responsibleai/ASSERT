<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import type { OutcomePlotRow } from './outcome-plot.js';

	let {
		rows,
		denominatorLabel
	}: {
		rows: OutcomePlotRow[];
		denominatorLabel: string;
	} = $props();

	type RowGroup = { factor: string; rows: OutcomePlotRow[] };

	const groups = $derived.by<RowGroup[]>(() => {
		const grouped = new Map<string, OutcomePlotRow[]>();
		for (const row of rows) {
			if (!grouped.has(row.factor)) grouped.set(row.factor, []);
			grouped.get(row.factor)!.push(row);
		}
		return [...grouped.entries()].map(([factor, groupRows]) => ({ factor, rows: groupRows }));
	});

	const hasData = $derived(rows.some((row) => row.n > 0));
	const groupKey = $derived(groups.map((group) => group.factor).join('\0'));
	let expandedFactors = $state<Record<string, boolean>>({ Overall: true });

	$effect(() => {
		groupKey;
		expandedFactors = { Overall: true };
	});

	function pct(value: number): string {
		return `${Math.round(value * 100)}%`;
	}

	function ciText(row: OutcomePlotRow): string {
		if (row.n === 0) return '-';
		return `${pct(row.rate)} [${pct(row.ciLow)}, ${pct(row.ciHigh)}]`;
	}

	function countText(row: OutcomePlotRow): string {
		return `${row.flagged} flagged / ${row.n} ${denominatorLabel}`;
	}

	function rateColor(rate: number): string {
		if (rate >= 0.5) return 'var(--color-score-fail)';
		if (rate > 0) return 'var(--color-score-border)';
		return 'var(--color-score-pass)';
	}

	function isGroupExpanded(factor: string): boolean {
		return factor === 'Overall' || expandedFactors[factor] === true;
	}

	function toggleGroup(factor: string) {
		if (factor === 'Overall') return;
		expandedFactors = { ...expandedFactors, [factor]: !expandedFactors[factor] };
	}
</script>

{#if hasData}
	<div class="rounded-lg border border-border bg-surface">
		<div class="hidden overflow-x-auto sm:block">
			<div class="min-w-[42rem]">
				<div class="grid grid-cols-[minmax(8rem,14rem)_minmax(12rem,1fr)_9rem] gap-3 border-b border-border bg-surface-2/45 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
					<div>Dimension level</div>
					<div>Rate</div>
					<div class="text-right">Rate (95% CI)</div>
				</div>
				<div class="divide-y divide-border/50">
					{#each groups as group (group.factor)}
						<div class="plot-group">
							{#if group.factor === 'Overall'}
								<div class="bg-bg/30 px-4 py-1.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
									{group.factor}
								</div>
							{:else}
								<button
									class="flex w-full items-center gap-2 bg-bg/30 px-4 py-1.5 text-left font-mono text-[10px] uppercase tracking-wider text-text-muted transition-colors hover:bg-bg/50 hover:text-text-secondary"
									aria-expanded={isGroupExpanded(group.factor)}
									onclick={() => toggleGroup(group.factor)}
								>
									<svg class="h-3 w-3 shrink-0 transition-transform {isGroupExpanded(group.factor) ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
										<path d="M9 5l7 7-7 7" />
									</svg>
									<span>{group.factor}</span>
									<span class="ml-auto normal-case tracking-normal text-text-muted/70">{group.rows.length} levels</span>
								</button>
							{/if}
							{#if isGroupExpanded(group.factor)}
								{#each group.rows as row (group.factor + ':' + row.level)}
									<div class="grid grid-cols-[minmax(8rem,14rem)_minmax(12rem,1fr)_9rem] items-center gap-3 px-4 py-2.5">
										<div class="min-w-0">
											<div class="truncate text-sm text-text-secondary" title={row.level}>{row.level}</div>
											<div class="mt-0.5 font-mono text-[10px] text-text-muted">
												{countText(row)}
											</div>
										</div>
										<div class="relative h-7">
											<div class="absolute left-0 right-0 top-1/2 h-px -translate-y-1/2 bg-border"></div>
											{#each [0, 0.25, 0.5, 0.75, 1] as tick}
												<div class="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-border/70" style="left: {tick * 100}%"></div>
											{/each}
											{#if row.n > 0}
												<div
													class="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full opacity-40"
													style="left: {row.ciLow * 100}%; width: {(row.ciHigh - row.ciLow) * 100}%; background: var(--color-text-muted)"
												></div>
												<div
													class="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface shadow"
													style="left: {row.rate * 100}%; background: {rateColor(row.rate)}"
												></div>
											{/if}
										</div>
										<div class="text-right font-mono text-[11px] tabular-nums text-text-secondary">
											{ciText(row)}
										</div>
									</div>
								{/each}
							{/if}
						</div>
					{/each}
				</div>
				<div class="grid grid-cols-[minmax(8rem,14rem)_minmax(12rem,1fr)_9rem] gap-3 border-t border-border/60 px-4 py-1.5 font-mono text-[9px] text-text-muted">
					<div></div>
					<div class="relative h-4">
						{#each [0, 0.25, 0.5, 0.75, 1] as tick}
							<span class="absolute -translate-x-1/2" style="left: {tick * 100}%">{Math.round(tick * 100)}%</span>
						{/each}
					</div>
					<div></div>
				</div>
			</div>
		</div>

		<div class="sm:hidden">
			<div class="grid grid-cols-[minmax(0,1fr)_7.5rem] gap-3 border-b border-border bg-surface-2/45 px-4 py-2 text-[10px] font-semibold uppercase tracking-wider text-text-muted">
				<div>Dimension level</div>
				<div class="text-right">Rate (95% CI)</div>
			</div>
			<div class="divide-y divide-border/50">
				{#each groups as group (group.factor)}
					<div>
						{#if group.factor === 'Overall'}
							<div class="bg-bg/30 px-4 py-1.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
								{group.factor}
							</div>
						{:else}
							<button
								class="flex w-full items-center gap-2 bg-bg/30 px-4 py-1.5 text-left font-mono text-[10px] uppercase tracking-wider text-text-muted transition-colors hover:bg-bg/50 hover:text-text-secondary"
								aria-expanded={isGroupExpanded(group.factor)}
								onclick={() => toggleGroup(group.factor)}
							>
								<svg class="h-3 w-3 shrink-0 transition-transform {isGroupExpanded(group.factor) ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
									<path d="M9 5l7 7-7 7" />
								</svg>
								<span>{group.factor}</span>
								<span class="ml-auto normal-case tracking-normal text-text-muted/70">{group.rows.length} levels</span>
							</button>
						{/if}
						{#if isGroupExpanded(group.factor)}
							{#each group.rows as row (group.factor + ':' + row.level)}
								<div class="px-4 py-3">
									<div class="flex items-start justify-between gap-3">
										<div class="min-w-0">
											<div class="truncate text-sm text-text-secondary" title={row.level}>{row.level}</div>
											<div class="mt-0.5 font-mono text-[10px] text-text-muted">
												{countText(row)}
											</div>
										</div>
										<div class="shrink-0 text-right font-mono text-[11px] tabular-nums text-text-secondary">
											{ciText(row)}
										</div>
									</div>
									<div class="relative mt-2 h-6">
										<div class="absolute left-0 right-0 top-1/2 h-px -translate-y-1/2 bg-border"></div>
										{#each [0, 0.25, 0.5, 0.75, 1] as tick}
											<div class="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-border/70" style="left: {tick * 100}%"></div>
										{/each}
										{#if row.n > 0}
											<div
												class="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full opacity-40"
												style="left: {row.ciLow * 100}%; width: {(row.ciHigh - row.ciLow) * 100}%; background: var(--color-text-muted)"
											></div>
											<div
												class="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface shadow"
												style="left: {row.rate * 100}%; background: {rateColor(row.rate)}"
											></div>
										{/if}
									</div>
								</div>
							{/each}
						{/if}
					</div>
				{/each}
			</div>
			<div class="border-t border-border/60 px-4 py-1.5 font-mono text-[9px] text-text-muted">
				<div class="relative h-4">
					{#each [0, 0.25, 0.5, 0.75, 1] as tick}
						<span class="absolute -translate-x-1/2" style="left: {tick * 100}%">{Math.round(tick * 100)}%</span>
					{/each}
				</div>
			</div>
		</div>
	</div>
{:else}
	<div class="rounded-lg border border-dashed border-border bg-surface px-4 py-8 text-center text-sm text-text-muted">
		No judged rows for this outcome.
	</div>
{/if}
