<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import { activeRuns } from '$lib/active-runs.js';
	import PrimerDropdown from '$lib/PrimerDropdown.svelte';
	import PrimerPagination from '$lib/PrimerPagination.svelte';

	let { data } = $props();
	let search = $state('');
	let statusFilter = $state<string>('all');
	let sortBy = $state<'newest' | 'oldest' | 'name' | 'runs'>('newest');
	let viewMode = $state<'card' | 'list'>('card');
	let listPage = $state(1);

	const PAGE_SIZE = 12;
	const statusConfig: Record<string, { icon: string; label: string; class: string }> = {
		systematized: { icon: '○', label: 'Behavior Categories Defined', class: 'text-text-muted' },
		test_set_ready: { icon: '●', label: 'Evaluation Test Set Generated', class: 'text-score-border' },
		has_results: { icon: '◉', label: 'Has Evaluation Result', class: 'text-score-pass' }
	};

	// Each pipeline stage is inclusive of the prior stages, so filtering by
	// "Behavior Categories Defined" should also surface suites that have
	// progressed further (test_set_ready, has_results) — they completed that
	// step too. Status rank gives us a single "≥" comparison.
	const statusRank: Record<string, number> = {
		systematized: 1,
		test_set_ready: 2,
		has_results: 3
	};

	let filtered = $derived.by(() => {
		let items = data.suites;
		if (search) {
			const q = search.toLowerCase();
			items = items.filter(
				(s) => s.suite_id.toLowerCase().includes(q) || s.behavior_name.toLowerCase().includes(q)
			);
		}
		if (statusFilter !== 'all') {
			if (statusFilter === 'empty') {
				items = items.filter((s) => s.status === 'empty');
			} else {
				const minRank = statusRank[statusFilter] ?? 0;
				items = items.filter((s) => (statusRank[s.status] ?? 0) >= minRank);
			}
		}
		items = [...items].sort((a, b) => {
			if (sortBy === 'newest') return (b.created_at ?? '').localeCompare(a.created_at ?? '');
			if (sortBy === 'oldest') return (a.created_at ?? '').localeCompare(b.created_at ?? '');
			if (sortBy === 'name') return a.behavior_name.localeCompare(b.behavior_name);
			if (sortBy === 'runs') return b.run_count - a.run_count;
			return 0;
		});
		return items;
	});

	let totalPages = $derived(Math.max(1, Math.ceil(filtered.length / PAGE_SIZE)));
	let paginatedList = $derived(filtered.slice((listPage - 1) * PAGE_SIZE, listPage * PAGE_SIZE));

	$effect(() => {
		search;
		statusFilter;
		sortBy;
		listPage = 1;
	});

	$effect(() => {
		if (listPage > totalPages) listPage = totalPages;
	});
</script>

<div class="mb-6 flex items-start justify-between gap-3">
	<div>
		<h1 class="text-xl font-semibold tracking-tight">Evaluation suites</h1>
		<p class="mt-0.5 text-sm text-text-muted">View evaluation test sets and results aligned to taxonomy-defined behavior categories.</p>
	</div>
	<a
		href="/new"
		class="btn btn-primary shrink-0 no-underline"
	>
		<span class="inline-flex items-center gap-1.5 whitespace-nowrap">
			<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M12 5v14M5 12h14"/></svg>
			<span>New evaluation</span>
		</span>
	</a>
</div>

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
					class="group flex flex-col gap-1.5 rounded-md border border-interactive/15 bg-bg/40 px-3 py-2.5 no-underline transition-colors hover:bg-interactive/10"
				>
					<div class="flex items-center justify-between gap-3">
						<div class="min-w-0 flex items-center gap-2.5">
							<span class="h-1.5 w-1.5 shrink-0 rounded-full bg-interactive"></span>
							<span class="truncate font-mono text-sm text-text">{run.suiteId}<span class="text-text-muted"> / </span>{run.runId}</span>
						</div>
						<svg class="h-3.5 w-3.5 shrink-0 text-text-muted transition-transform group-hover:translate-x-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>
					</div>
					<div class="flex flex-wrap items-center gap-x-3 gap-y-1 pl-4 text-[11px] text-text-muted">
						{#if run.currentStage}
							<span class="rounded-full border border-interactive/20 bg-interactive/10 px-1.5 py-0.5 font-medium text-interactive">{run.currentStage}</span>
						{/if}
					</div>
				</a>
			{/each}
		</div>
	</section>
{/if}

<div class="mb-4 flex items-center justify-between gap-3">
	<div class="flex min-w-0 flex-1 items-center gap-3">
		<div class="relative" style="flex: 1 1 auto; max-width: 480px; min-width: 240px;">
			<label for="suite-search" class="sr-only">Search</label>
			<svg class="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor">
				<path d="M10.68 11.74a6 6 0 0 1-7.922-8.982 6 6 0 0 1 8.982 7.922l3.04 3.04a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215ZM11.5 7a4.499 4.499 0 1 0-8.997 0A4.499 4.499 0 0 0 11.5 7Z" />
			</svg>
			<input
				id="suite-search"
				type="text"
				placeholder="Search evaluation suites…"
				bind:value={search}
				class="form-control w-full"
				style="padding-left: 2rem;"
			/>
		</div>
		<div class="flex-shrink-0">
			<PrimerDropdown
				label="Status"
				ariaLabel="Filter by evaluation status"
				options={[
					{ value: 'all', label: 'All statuses' },
					{ value: 'empty', label: 'No behavior categories' },
					{ value: 'systematized', label: '○ Behavior Categories Defined' },
					{ value: 'test_set_ready', label: '● Evaluation Test Set Generated' },
					{ value: 'has_results', label: '◉ Has Evaluation Result' }
				]}
				selected={statusFilter}
				onSelect={(v) => statusFilter = v}
			/>
		</div>
		<div class="flex-shrink-0">
			<PrimerDropdown
				label="Sort"
				ariaLabel="Sort evaluation suites"
				options={[
					{ value: 'newest', label: 'Newest first' },
					{ value: 'oldest', label: 'Oldest first' },
					{ value: 'name', label: 'Name A–Z' },
					{ value: 'runs', label: 'Most runs' }
				]}
				selected={sortBy}
				onSelect={(v) => sortBy = v as typeof sortBy}
			/>
		</div>
		<span class="flex-shrink-0 text-[11px] text-text-muted">{filtered.length} suites</span>
	</div>
	<div class="flex flex-shrink-0 items-center">
		<div class="btn-group-attached">
			<button
				type="button"
				class="btn btn-icon-small {viewMode === 'card' ? 'btn-primary' : ''}"
				aria-label="Card view"
				aria-pressed={viewMode === 'card'}
				onclick={() => viewMode = 'card'}
			>
				<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<rect x="3" y="3" width="7" height="7" rx="1" />
					<rect x="14" y="3" width="7" height="7" rx="1" />
					<rect x="3" y="14" width="7" height="7" rx="1" />
					<rect x="14" y="14" width="7" height="7" rx="1" />
				</svg>
			</button>
			<button
				type="button"
				class="btn btn-icon-small {viewMode === 'list' ? 'btn-primary' : ''}"
				aria-label="List view"
				aria-pressed={viewMode === 'list'}
				onclick={() => { viewMode = 'list'; listPage = 1; }}
			>
				<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
					<path d="M4 6h16M4 12h16M4 18h16" />
				</svg>
			</button>
		</div>
	</div>
</div>

{#if filtered.length === 0}
	<div class="rounded-lg border border-border bg-surface px-6 py-12 text-center">
		<svg class="mx-auto mb-3 h-8 w-8 text-text-muted opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
			<path d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/>
		</svg>
		<p class="text-sm text-text-secondary">No evaluation suites found</p>
		<p class="mt-1 text-xs text-text-muted">Try a different search or filter.</p>
	</div>
{:else if viewMode === 'card'}
	<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
		{#each filtered as suite (suite.suite_id)}
			{@const sc = statusConfig[suite.status]}
			<div class="card-hover group relative isolate rounded-lg border border-border bg-surface p-4 no-underline">
				<div class="flex items-start justify-between gap-3">
					<p class="min-w-0 flex-1 flex items-center gap-1.5 pt-0.5 font-mono text-[10px] uppercase leading-4 tracking-wider text-text-muted">
						<svg class="h-3 w-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/></svg>
						<span class="truncate">{suite.suite_id}</span>
					</p>
					{#if sc}
						<span class="inline-flex shrink-0 items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] leading-4 {sc.class}">
							<span>{sc.icon}</span> {sc.label}
						</span>
					{/if}
				</div>
				<a
					href="/suite/{suite.suite_id}"
					title={suite.behavior_name}
					class="card-heading mt-1 block break-words text-base font-semibold leading-tight text-text no-underline line-clamp-2 after:absolute after:inset-0 after:content-['']"
				>
					{suite.behavior_name}
				</a>
				<div class="relative z-10 mt-4 grid grid-cols-3 gap-2 rounded-md bg-surface py-2">
					<a href="/suite/{suite.suite_id}?section=policy" class="no-underline hover:text-interactive">
						<div class="text-[10px] text-text-muted">Behavior categories</div>
						<div class="mt-1 text-sm text-text-secondary">{suite.behavior_category_count}</div>
					</a>
					<a href="/suite/{suite.suite_id}?section=seeds" class="no-underline hover:text-interactive">
						<div class="text-[10px] text-text-muted">Evaluation test set</div>
						<div class="mt-1 text-sm text-text-secondary">{suite.prompt_test_case_count + suite.scenario_test_case_count}</div>
					</a>
					<a href="/suite/{suite.suite_id}?section=results" class="no-underline hover:text-interactive">
						<div class="text-[10px] text-text-muted">Evaluation results</div>
						<div class="mt-1 text-sm text-text-secondary">{suite.run_count}</div>
					</a>
				</div>
				{#if suite.created_at}
					<div class="mt-2 flex items-center gap-1 text-[11px] text-text-muted">
						<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
						Created {new Date(suite.created_at).toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })}
					</div>
				{/if}
			</div>
		{/each}
	</div>
{:else}
	<div class="overflow-hidden rounded-lg border border-border">
		<table class="w-full">
			<thead>
				<tr class="border-b border-border bg-surface text-left text-[11px] text-text-muted">
					<th class="px-4 py-2 font-semibold">Suite name</th>
					<th class="px-4 py-2 font-semibold">Suite created at</th>
					<th class="px-4 py-2 font-semibold">Behavior categories</th>
					<th class="px-4 py-2 font-semibold">Evaluation test set</th>
					<th class="px-4 py-2 font-semibold">Evaluation results</th>
					<th class="px-4 py-2 font-semibold">Status</th>
				</tr>
			</thead>
			<tbody>
				{#each paginatedList as suite}
					{@const sc = statusConfig[suite.status]}
					<tr class="border-b border-border transition-colors last:border-b-0 hover:bg-surface">
						<td class="max-w-xs px-4 py-2.5 align-middle">
							<a
								href="/suite/{suite.suite_id}"
								title={suite.behavior_name}
								class="card-heading mt-1.5 block break-words text-base font-semibold leading-tight text-text no-underline line-clamp-2 hover:text-interactive hover:underline"
							>{suite.behavior_name}</a>
							<p class="truncate font-mono text-[10px] text-text-muted" style="margin:0 0 6px;">{suite.suite_id}</p>
						</td>
						<td class="px-4 py-2.5 align-middle text-sm text-text-muted">{suite.created_at ? new Date(suite.created_at).toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' }) : '—'}</td>
						<td class="px-4 py-2.5 align-middle text-sm text-text-muted">{suite.behavior_category_count}</td>
						<td class="px-4 py-2.5 align-middle text-sm text-text-muted">{suite.prompt_test_case_count + suite.scenario_test_case_count}</td>
						<td class="px-4 py-2.5 align-middle text-sm text-text-muted">{suite.run_count}</td>
						<td class="px-4 py-2.5 align-middle">
							{#if sc}
								<span class="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] {sc.class}"><span>{sc.icon}</span> {sc.label}</span>
							{:else}
								<span class="text-[10px] text-text-muted">—</span>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	{#if totalPages > 1}
		<PrimerPagination page={listPage} {totalPages} onPageChange={(n) => (listPage = n)} />
	{/if}
{/if}
