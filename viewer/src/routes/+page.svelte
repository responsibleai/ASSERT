<script lang="ts">
	import { activeRuns } from '$lib/active-runs.js';

	let { data } = $props();
	let search = $state('');
	let statusFilter = $state<string>('all');
	let sortBy = $state<'newest' | 'oldest' | 'name' | 'runs'>('newest');
	let viewMode = $state<'card' | 'list'>('card');
	let listPage = $state(1);

	const PAGE_SIZE = 10;
	const statusConfig: Record<string, { icon: string; label: string; class: string }> = {
		systematized: { icon: '○', label: 'Behavior Categories Ready', class: 'text-text-muted' },
		test_set_ready: { icon: '●', label: 'Test Set Generated', class: 'text-score-border' },
		has_results: { icon: '◉', label: 'Has Run Result', class: 'text-score-pass' }
	};

	let filtered = $derived.by(() => {
		let items = data.suites;
		if (search) {
			const q = search.toLowerCase();
			items = items.filter(
				(s) => s.suite_id.toLowerCase().includes(q) || s.behavior_name.toLowerCase().includes(q)
			);
		}
		if (statusFilter !== 'all') items = items.filter((s) => s.status === statusFilter);
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

<div class="mb-6 flex items-start justify-between">
	<div>
		<h1 class="text-xl font-semibold tracking-tight">Measurement suites</h1>
		<p class="mt-0.5 text-sm text-text-muted">Browse behavior categories, test cases, and measurement results.</p>
	</div>
	<a
		href="/new"
		class="btn btn-primary btn-small no-underline"
	>
		+ New evaluation run
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
				placeholder="Search measurement suites…"
				bind:value={search}
				class="form-control w-full"
				style="padding-left: 2rem;"
			/>
		</div>
		<div class="flex-shrink-0">
			<label for="suite-status-filter" class="sr-only">Status</label>
			<select id="suite-status-filter" bind:value={statusFilter} class="form-select">
				<option value="all">All statuses</option>
				<option value="systematized">○ Behavior Categories Ready</option>
				<option value="test_set_ready">● Test Set Generated</option>
				<option value="has_results">◉ Has Run Result</option>
			</select>
		</div>
		<div class="flex-shrink-0">
			<label for="suite-sort" class="sr-only">Sort</label>
			<select id="suite-sort" bind:value={sortBy} class="form-select">
				<option value="newest">Newest first</option>
				<option value="oldest">Oldest first</option>
				<option value="name">Name A–Z</option>
				<option value="runs">Most runs</option>
			</select>
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
		<p class="text-sm text-text-secondary">No measurement suites found</p>
		<p class="mt-1 text-xs text-text-muted">Try a different search or filter.</p>
	</div>
{:else if viewMode === 'card'}
	<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
		{#each filtered as suite (suite.suite_id)}
			{@const sc = statusConfig[suite.status] ?? statusConfig.systematized}
			<div class="card-hover group rounded-lg border border-border bg-surface p-4 no-underline">
				<div class="flex items-center justify-between gap-3">
					<p class="truncate font-mono text-[10px] uppercase tracking-wider text-text-muted">{suite.suite_id}</p>
					<span class="inline-flex shrink-0 items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] {sc.class}">
						<span>{sc.icon}</span> {sc.label}
					</span>
				</div>
				<a href="/suite/{suite.suite_id}" class="card-heading mt-1 block text-base font-semibold text-text no-underline">
					{suite.behavior_name}
				</a>
				<div class="mt-4 grid grid-cols-3 gap-2 rounded-md bg-surface py-2">
					<a href="/suite/{suite.suite_id}?section=taxonomy" class="no-underline hover:text-interactive">
						<div class="text-[10px] text-text-muted">Categories</div>
						<div class="mt-1 text-sm text-text-secondary">{suite.behavior_category_count}</div>
					</a>
					<a href="/suite/{suite.suite_id}?section=test_set" class="no-underline hover:text-interactive">
						<div class="text-[10px] text-text-muted">Test cases</div>
						<div class="mt-1 text-sm text-text-secondary">{suite.prompt_test_case_count + suite.scenario_test_case_count}</div>
					</a>
					<a href="/suite/{suite.suite_id}?section=results" class="no-underline hover:text-interactive">
						<div class="text-[10px] text-text-muted">Evaluations</div>
						<div class="mt-1 text-sm text-text-secondary">{suite.run_count}</div>
					</a>
				</div>
				{#if suite.created_at}
					<div class="mt-2 flex items-center gap-1 text-[11px] text-text-muted">
						<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
						{new Date(suite.created_at).toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' })}
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
					<th class="px-4 py-2 font-semibold">Categories</th>
					<th class="px-4 py-2 font-semibold">Test cases</th>
					<th class="px-4 py-2 font-semibold">Evaluations</th>
					<th class="px-4 py-2 font-semibold">Status</th>
				</tr>
			</thead>
			<tbody>
				{#each paginatedList as suite}
					{@const sc = statusConfig[suite.status] ?? statusConfig.systematized}
					<tr class="border-b border-border transition-colors last:border-b-0 hover:bg-surface">
						<td class="px-4 py-2.5 align-middle">
							<a href="/suite/{suite.suite_id}" class="card-heading text-base font-semibold no-underline hover:text-interactive hover:underline">{suite.behavior_name}</a>
							<p class="mt-0.5 truncate font-mono text-[10px] text-text-muted">{suite.suite_id}</p>
						</td>
						<td class="px-4 py-2.5 align-middle"><a href="/suite/{suite.suite_id}?section=taxonomy" class="text-sm text-text-secondary no-underline hover:text-interactive hover:underline">{suite.behavior_category_count}</a></td>
						<td class="px-4 py-2.5 align-middle"><a href="/suite/{suite.suite_id}?section=test_set" class="text-sm text-text-secondary no-underline hover:text-interactive hover:underline">{suite.prompt_test_case_count + suite.scenario_test_case_count}</a></td>
						<td class="px-4 py-2.5 align-middle"><a href="/suite/{suite.suite_id}?section=results" class="text-sm text-text-secondary no-underline hover:text-interactive hover:underline">{suite.run_count}</a></td>
						<td class="px-4 py-2.5 align-middle">
							<span class="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] {sc.class}"><span>{sc.icon}</span> {sc.label}</span>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	{#if totalPages > 1}
		<nav class="mt-4 flex items-center justify-between" aria-label="Pagination">
			<span class="text-xs text-text-muted">Showing {(listPage - 1) * PAGE_SIZE + 1}-{Math.min(listPage * PAGE_SIZE, filtered.length)} of {filtered.length}</span>
			<div class="flex items-center gap-1">
				<button class="rounded-md border border-border px-2 py-1 text-xs disabled:opacity-50" disabled={listPage <= 1} onclick={() => listPage -= 1}>Previous</button>
				{#each Array.from({ length: totalPages }, (_, i) => i + 1) as pageNum}
					<button class="rounded-md border border-border px-2 py-1 text-xs {pageNum === listPage ? 'bg-interactive text-white' : 'text-text-muted'}" onclick={() => listPage = pageNum}>{pageNum}</button>
				{/each}
				<button class="rounded-md border border-border px-2 py-1 text-xs disabled:opacity-50" disabled={listPage >= totalPages} onclick={() => listPage += 1}>Next</button>
			</div>
		</nav>
	{/if}
{/if}
