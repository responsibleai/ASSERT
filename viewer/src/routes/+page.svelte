<script lang="ts">
	import { activeRuns } from '$lib/active-runs.js';

	let { data } = $props();
	let search = $state('');
	let statusFilter = $state<string>('all');
	let sortBy = $state<'newest' | 'oldest' | 'name' | 'runs'>('newest');

	const statusConfig: Record<string, { icon: string; label: string; class: string }> = {
		policy_only: { icon: '○', label: 'Policy only', class: 'text-text-muted' },
		seeds_ready: { icon: '●', label: 'Seeds ready', class: 'text-score-border' },
		has_results: { icon: '◉', label: 'Has results', class: 'text-score-pass' }
	};

	let filtered = $derived.by(() => {
		let items = data.suites;
		if (search) {
			const q = search.toLowerCase();
			items = items.filter(
				(s) => s.suite_id.toLowerCase().includes(q) || s.concept_name.toLowerCase().includes(q)
			);
		}
		if (statusFilter !== 'all') {
			items = items.filter((s) => s.status === statusFilter);
		}
		items = [...items].sort((a, b) => {
			if (sortBy === 'newest') return (b.created_at ?? '').localeCompare(a.created_at ?? '');
			if (sortBy === 'oldest') return (a.created_at ?? '').localeCompare(b.created_at ?? '');
			if (sortBy === 'name') return a.concept_name.localeCompare(b.concept_name);
			if (sortBy === 'runs') return b.run_count - a.run_count;
			return 0;
		});
		return items;
	});
</script>

<!-- Page header -->
<div class="mb-6">
	<div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
		<div>
			<h1 class="text-xl font-semibold tracking-tight">Measurement Suites</h1>
			<p class="mt-1 text-sm text-text-muted">Browse concept policies, seeds, and measurement results.</p>
		</div>
	</div>
</div>

<!-- Active Runs -->
{#if $activeRuns.length > 0}
<section class="mb-5 rounded-lg border border-interactive/20 bg-interactive/5 p-3">
	<div class="mb-2 flex items-center justify-between gap-2 px-1">
		<div class="flex items-center gap-2">
			<span class="h-2 w-2 animate-pulse rounded-full bg-interactive"></span>
			<h2 class="text-xs font-semibold uppercase tracking-wider text-interactive">Running now</h2>
		</div>
		<span class="text-[11px] text-text-muted">{$activeRuns.length} active</span>
	</div>
	<div class="space-y-2">
		{#each $activeRuns as run}
			<a
				href="/suite/{run.suiteId}/{run.runId}/monitor"
				class="group flex flex-col gap-1.5 rounded-md border border-interactive/15 bg-bg/40 px-3 py-2.5 transition-colors hover:bg-interactive/10"
			>
				<div class="flex items-center justify-between gap-3">
					<div class="min-w-0 flex items-center gap-2.5">
						<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-interactive"></span>
						<span class="truncate font-mono text-sm text-text">
							{run.suiteId}<span class="text-text-muted"> / </span>{run.runId}
						</span>
					</div>
					<svg class="h-3.5 w-3.5 shrink-0 text-text-muted transition-transform group-hover:translate-x-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>
				</div>
				<div class="flex flex-wrap items-center gap-x-3 gap-y-1 pl-4 text-[11px] text-text-muted">
					{#if run.currentStage}
						<span class="rounded-full border border-interactive/20 bg-interactive/10 px-1.5 py-0.5 font-medium text-interactive">
							{run.currentStage}
						</span>
					{/if}
					<span>{Object.values(run.stages).filter((s) => s === 'completed').length}/{Object.keys(run.stages).length} complete</span>
					<span>Started {run.startedAt ? new Date(run.startedAt).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—'}</span>
				</div>
			</a>
		{/each}
	</div>
</section>
{/if}

<!-- Filters -->
<div class="mb-4 rounded-lg border border-border bg-surface p-3">
	<div class="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
		<div class="min-w-0 flex-1">
			<label for="suite-search" class="block text-[11px] font-medium text-text-muted">Search</label>
			<div class="relative mt-1.5">
				<svg class="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
				</svg>
				<input
					id="suite-search"
					type="text"
					placeholder="Search measurement suites…"
					bind:value={search}
					class="w-full rounded-md border border-border bg-bg py-2 pl-8 pr-3 text-sm text-text placeholder-text-muted outline-none transition-colors focus:border-interactive focus:ring-1 focus:ring-interactive/50"
				/>
			</div>
		</div>
		<div class="grid gap-3 sm:grid-cols-2 lg:min-w-[22rem]">
			<div>
				<label for="suite-status-filter" class="block text-[11px] font-medium text-text-muted">Status</label>
				<select
					id="suite-status-filter"
					bind:value={statusFilter}
					class="mt-1.5 w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text outline-none transition-colors focus:border-interactive"
				>
					<option value="all">All statuses</option>
					<option value="policy_only">○ Policy only</option>
					<option value="seeds_ready">● Seeds ready</option>
					<option value="has_results">◉ Has results</option>
				</select>
			</div>
			<div>
				<label for="suite-sort" class="block text-[11px] font-medium text-text-muted">Sort</label>
				<select
					id="suite-sort"
					bind:value={sortBy}
					class="mt-1.5 w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text outline-none transition-colors focus:border-interactive"
				>
					<option value="newest">Newest first</option>
					<option value="oldest">Oldest first</option>
					<option value="name">Name A–Z</option>
					<option value="runs">Most runs</option>
				</select>
			</div>
		</div>
	</div>
	<div class="mt-2 text-[11px] text-text-muted">{filtered.length} suites</div>
</div>

<!-- Suite cards -->
{#if filtered.length === 0}
<div class="rounded-lg border border-border bg-surface px-6 py-12 text-center">
	<svg class="mx-auto mb-3 h-8 w-8 text-text-muted opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
		<path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
	</svg>
	<p class="text-sm text-text-secondary">No measurement suites found</p>
	<p class="mt-1 text-xs text-text-muted">Try a different search or filter.</p>
</div>
{:else}
<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
	{#each filtered as suite (suite.suite_id)}
		<a
			href="/suite/{suite.suite_id}"
			class="group rounded-lg border border-border bg-surface p-4 transition-all hover:border-interactive/50 hover:shadow-sm"
		>
			<div class="flex items-start justify-between gap-3">
				<div class="min-w-0">
					<p class="truncate font-mono text-[10px] uppercase tracking-wider text-text-muted">{suite.suite_id}</p>
					<h2 class="mt-1 text-sm font-semibold text-text group-hover:text-interactive">{suite.concept_name}</h2>
				</div>
				<span class="inline-flex shrink-0 items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] {statusConfig[suite.status].class}">
					<span>{statusConfig[suite.status].icon}</span>
					{statusConfig[suite.status].label}
				</span>
			</div>
			<div class="mt-4 grid grid-cols-3 gap-2 rounded-md bg-bg/60 px-3 py-2">
				<div>
					<div class="text-[10px] uppercase tracking-wider text-text-muted">Categories</div>
					<div class="mt-1 text-sm text-text-secondary">{suite.behavior_count}</div>
				</div>
				<div>
					<div class="text-[10px] uppercase tracking-wider text-text-muted">Seeds</div>
					<div class="mt-1 text-sm text-text-secondary">{suite.seed_count + suite.scenario_seed_count}</div>
				</div>
				<div>
					<div class="text-[10px] uppercase tracking-wider text-text-muted">Runs</div>
					<div class="mt-1 text-sm text-text-secondary">{suite.run_count}</div>
				</div>
			</div>
			<div class="mt-3 flex items-center justify-between gap-2 text-[11px] text-text-muted">
				<span>{statusConfig[suite.status].label}</span>
				{#if suite.created_at}
					<span class="flex items-center gap-1.5">
						<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
						{new Date(suite.created_at).toLocaleDateString()}
					</span>
				{/if}
			</div>
		</a>
	{/each}
</div>
{/if}
