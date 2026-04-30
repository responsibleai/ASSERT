<script lang="ts">
	import ResultDrawer from '$lib/ResultDrawer.svelte';
	import { getRecordFlag, getRequiredBaseMetricNames } from '$lib/judgment.js';
	import { renderMarkdown } from '$lib/markdown.js';
	import { mergeRunLists, normalizePromptSeeds, normalizeScenarioSeeds, type CombinedRunEntry } from '$lib/suite-view.js';
	import type { DimensionDef, JudgedSample, ViewerResultItem } from '$lib/types.js';

	let { data } = $props();

	let descExpanded = $state(false);
	let metaOpen = $state(false);
	let selectedBehavior = $state<string | null>(null);
	let panelTab = $state<'definition' | 'seeds' | 'evaluations'>('definition');
	let selectedCompareRuns = $state<Set<string>>(new Set());
	let expandedRunIds = $state<Set<string>>(new Set());
	let behaviorEvalSamples = $state<JudgedSample[]>([]);
	let behaviorEvalLoading = $state(false);
	let behaviorEvalError = $state<string | null>(null);
	let behaviorEvalRunId = $state<string | null>(null);
	let drawerItem = $state<ViewerResultItem | null>(null);
	let drawerNavIdx = $state(-1);
	let drawerLoading = $state(false);
	let drawerCache = $state<Record<string, ViewerResultItem>>({});

	let requiredBaseMetrics = $derived(
		getRequiredBaseMetricNames((data.dimensionDefs ?? {}) as Record<string, DimensionDef>)
	);
	let metricNames = $derived(Object.keys((data.dimensionDefs ?? {}) as Record<string, DimensionDef>));
	let primaryMetric = $derived(metricNames[0] ?? 'policy_violation');
	let sortedBehaviors = $derived(data.policy?.behaviors ?? []);
	let promptSeedItems = $derived(normalizePromptSeeds(data.promptSeeds));
	let scenarioSeedItems = $derived(normalizeScenarioSeeds(data.scenarioSeeds));
	let allRuns = $derived(mergeRunLists(data.runs, data.auditRuns));
	let conceptName = $derived(data.policy?.concept?.name ?? data.policy?.risk?.name ?? data.suite_id);
	let conceptDef = $derived(data.policy?.concept?.definition ?? data.policy?.risk?.definition ?? '');
	let summaryItemCount = $derived(Array.isArray(data.systematization?.summary_items) ? data.systematization.summary_items.length : 0);
	let systematizationMode = $derived(systematizationModeFor(data.systematization));
	let hasSystematization = $derived(Boolean(data.systematization));
	let canCompare = $derived(selectedCompareRuns.size >= 2);
	const panelTabs = ['definition', 'seeds', 'evaluations'] as const;

	let promptCountsByBehavior = $derived.by(() => {
		const map = new Map<string, number>();
		for (const seed of promptSeedItems) map.set(seed.behavior, (map.get(seed.behavior) ?? 0) + 1);
		return map;
	});

	let scenarioCountsByBehavior = $derived.by(() => {
		const map = new Map<string, number>();
		for (const seed of scenarioSeedItems) map.set(seed.behavior, (map.get(seed.behavior) ?? 0) + 1);
		return map;
	});

	let selectedBehaviorData = $derived.by(() => {
		if (!selectedBehavior) return null;
		const idx = sortedBehaviors.findIndex((behavior) => behavior.name === selectedBehavior);
		if (idx < 0) return null;
		const behavior = sortedBehaviors[idx];
		return {
			...behavior,
			idx,
			promptCount: promptSeedItems.filter((seed) => seed.behavior === behavior.name).length,
			scenarioCount: scenarioSeedItems.filter((seed) => seed.behavior === behavior.name).length
		};
	});

	let selectedBehaviorPrompts = $derived(selectedBehavior ? promptSeedItems.filter((seed) => seed.behavior === selectedBehavior) : []);
	let selectedBehaviorScenarios = $derived(selectedBehavior ? scenarioSeedItems.filter((seed) => seed.behavior === selectedBehavior) : []);

	$effect(() => {
		if (allRuns.length <= 3) expandedRunIds = new Set(allRuns.map((run) => run.run_id));
		else expandedRunIds = new Set();
	});

	function metricLabel(metric: string): string {
		const label = metric.replace(/_/g, ' ');
		return label.charAt(0).toUpperCase() + label.slice(1);
	}

	function metricRateClass(rate: number | null): string {
		if (rate == null) return 'text-text-muted';
		if (rate >= 0.5) return 'text-score-fail';
		if (rate > 0) return 'text-score-border';
		return 'text-score-pass';
	}

	function systematizationModeFor(systematization: Record<string, unknown> | null | undefined): string | null {
		const meta = systematization?.meta;
		if (!meta || typeof meta !== 'object' || Array.isArray(meta)) return null;
		const mode = (meta as Record<string, unknown>).mode;
		return typeof mode === 'string' ? mode : null;
	}

	function toggleCompareRun(compareId: string | null) {
		if (!compareId) return;
		const next = new Set(selectedCompareRuns);
		if (next.has(compareId)) next.delete(compareId);
		else next.add(compareId);
		selectedCompareRuns = next;
	}

	function toggleRunExpanded(runId: string) {
		const next = new Set(expandedRunIds);
		if (next.has(runId)) next.delete(runId);
		else next.add(runId);
		expandedRunIds = next;
	}

	function aggregateRunViolationRate(run: CombinedRunEntry): number | null {
		const promptTotal = run.prompt?.metrics?.total ?? 0;
		const auditTotal = run.audit?.metrics?.total ?? 0;
		const total = promptTotal + auditTotal;
		if (total === 0) return null;
		const promptViolations = promptTotal * (run.prompt?.metrics?.policy_violation_rate ?? 0);
		const auditViolations = auditTotal * (run.audit?.metrics?.policy_violation_rate ?? 0);
		return (promptViolations + auditViolations) / total;
	}

	function runTotal(run: CombinedRunEntry): number {
		return (run.prompt?.metrics?.total ?? 0) + (run.audit?.metrics?.total ?? 0);
	}

	function runTarget(run: CombinedRunEntry): string {
		return run.prompt?.metrics?.target ?? run.audit?.metrics?.target ?? '—';
	}

	async function loadBehaviorEvalResults(behavior: string) {
		behaviorEvalLoading = true;
		behaviorEvalError = null;
		behaviorEvalSamples = [];
		const promptRun = allRuns.find((run) => run.prompt !== null);
		if (!promptRun) {
			behaviorEvalError = 'No prompt evaluation runs available.';
			behaviorEvalLoading = false;
			return;
		}
		const runId = promptRun.prompt_run_id ?? promptRun.run_id;
		behaviorEvalRunId = runId;
		try {
			const res = await fetch(`/api/runs/${encodeURIComponent(data.suite_id)}/${encodeURIComponent(runId)}/samples?behavior=${encodeURIComponent(behavior)}`);
			if (!res.ok) throw new Error('Failed to load results');
			behaviorEvalSamples = await res.json();
		} catch (error) {
			behaviorEvalError = error instanceof Error ? error.message : 'Failed to load results';
		} finally {
			behaviorEvalLoading = false;
		}
	}

	function selectBehavior(name: string) {
		if (selectedBehavior === name) {
			selectedBehavior = null;
			panelTab = 'definition';
			return;
		}
		selectedBehavior = name;
		panelTab = 'definition';
		void loadBehaviorEvalResults(name);
	}

	function selectPanelTab(tab: 'definition' | 'seeds' | 'evaluations') {
		panelTab = tab;
		if (tab === 'evaluations' && selectedBehavior) void loadBehaviorEvalResults(selectedBehavior);
	}

	function closeSidePanel() {
		selectedBehavior = null;
		panelTab = 'definition';
		behaviorEvalSamples = [];
		behaviorEvalError = null;
	}

	function sampleComplianceStatus(sample: JudgedSample): 'flagged' | 'compliant' | 'error' | 'pending' {
		if (sample.judge_error || sample.judge_status === 'judge_failed') return 'error';
		if (!sample.verdict) return 'pending';
		return getRecordFlag(sample, 'policy_violation') === true ? 'flagged' : 'compliant';
	}

	async function openEvalDrawer(sample: JudgedSample, idx: number) {
		if (!behaviorEvalRunId || !sample.seed_id) return;
		drawerNavIdx = idx;
		const cacheKey = `${data.suite_id}:${behaviorEvalRunId}:${sample.seed_id}`;
		if (drawerCache[cacheKey]) {
			drawerItem = drawerCache[cacheKey];
			return;
		}
		drawerLoading = true;
		try {
			const res = await fetch(`/api/runs/${encodeURIComponent(data.suite_id)}/${encodeURIComponent(behaviorEvalRunId)}/prompt/${encodeURIComponent(sample.seed_id)}`);
			if (!res.ok) throw new Error('Failed to load result');
			const item = await res.json();
			drawerCache = { ...drawerCache, [cacheKey]: item };
			drawerItem = item;
		} catch {
			drawerItem = null;
		} finally {
			drawerLoading = false;
		}
	}

	function closeDrawer() {
		drawerItem = null;
		drawerNavIdx = -1;
	}

	function navigateDrawer(delta: number) {
		const next = drawerNavIdx + delta;
		if (next < 0 || next >= behaviorEvalSamples.length) return;
		void openEvalDrawer(behaviorEvalSamples[next], next);
	}
</script>

<div class="mb-6">
	<nav aria-label="Breadcrumb">
		<ol class="Breadcrumb">
			<li class="Breadcrumb-item"><a href="/">Measurement suites</a></li>
			<li class="Breadcrumb-item" aria-current="page">{conceptName}</li>
		</ol>
	</nav>
	<div class="mt-2 flex w-full items-start justify-between gap-4">
		<div class="min-w-0 flex-1">
			<h1 class="text-xl font-semibold tracking-tight">{conceptName}</h1>
			<span class="mt-1.5 inline-block rounded bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-text-muted">{data.suite_id}</span>
			{#if conceptDef}
				<p class="mt-2 max-w-2xl text-sm leading-relaxed text-text-secondary {descExpanded ? '' : 'line-clamp-2'}">
					{conceptDef}
				</p>
				{#if conceptDef.length > 80}
					<button class="mt-0.5 text-xs text-interactive hover:text-interactive-hover" onclick={() => descExpanded = !descExpanded}>{descExpanded ? 'show less' : 'show more'}</button>
				{/if}
			{/if}
		</div>
	</div>
	<div class="mt-3 flex items-center gap-2">
		<span class="inline-flex items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 text-xs text-text-muted">
			{data.suite?.created_at ? new Date(data.suite.created_at).toLocaleDateString() : '—'}
		</span>
		<span class="inline-flex items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 text-xs text-text-muted">
			{sortedBehaviors.length} categories · {promptSeedItems.length + scenarioSeedItems.length} evaluation sets · {allRuns.length} evaluations
		</span>
		{#if hasSystematization}
			<button class="inline-flex items-center gap-1 rounded-full bg-surface px-2.5 py-1 text-xs text-text-muted transition-colors hover:text-text-secondary" onclick={() => metaOpen = !metaOpen}>
				{metaOpen ? 'hide details' : 'details'}
			</button>
		{/if}
	</div>
	{#if metaOpen && hasSystematization}
		<div class="mt-3 max-w-2xl rounded-lg border border-border bg-surface p-4">
			<h4 class="mb-1.5 text-xs font-semibold uppercase tracking-wider text-text-muted">Systematization artifacts</h4>
			<div class="flex flex-wrap gap-x-4 gap-y-1">
				<span class="text-xs text-text-muted"><span class="text-text-secondary">systematization:</span> present</span>
				{#if systematizationMode}<span class="text-xs text-text-muted"><span class="text-text-secondary">mode:</span> {systematizationMode}</span>{/if}
				{#if summaryItemCount > 0}<span class="text-xs text-text-muted"><span class="text-text-secondary">pattern summaries:</span> {summaryItemCount}</span>{/if}
				<span class="text-xs text-text-muted"><span class="text-text-secondary">behavior categories:</span> {sortedBehaviors.length}</span>
			</div>
		</div>
	{/if}
</div>

<div class="mb-6">
	<div class="sticky top-12 z-10 mb-3 flex w-full items-center bg-bg py-2">
		<h2 class="text-sm font-semibold text-text">Evaluation results</h2>
		<div class="ml-auto flex items-center gap-2">
			{#if selectedCompareRuns.size > 0}<span class="text-xs text-text-muted">{selectedCompareRuns.size} selected</span>{/if}
			{#if canCompare}
				<a href="/suite/{data.suite_id}/compare?runs={[...selectedCompareRuns].join(',')}" class="rounded-md border border-border px-3 py-1.5 text-xs text-text no-underline hover:border-interactive hover:text-interactive">Compare</a>
			{:else if selectedCompareRuns.size > 0}
				<button class="rounded-md border border-border px-3 py-1.5 text-xs text-text-muted opacity-60" disabled>Compare</button>
			{/if}
			{#if selectedCompareRuns.size > 0}<button class="rounded-md border border-border px-3 py-1.5 text-xs text-text-muted hover:text-text" onclick={() => selectedCompareRuns = new Set()}>Clear</button>{/if}
		</div>
	</div>

	{#if allRuns.length === 0}
		<div class="rounded-lg border border-border bg-surface px-6 py-8 text-center">
			<p class="text-sm text-text-secondary">No evaluation results yet.</p>
		</div>
	{:else}
		<div class="overflow-hidden rounded-lg border border-border">
			<table class="w-full text-left text-sm">
				<thead>
					<tr class="border-b border-border bg-surface">
						<th class="w-8 px-3 py-2 text-xs font-medium text-text-muted"></th>
						<th class="px-3 py-2 text-xs font-medium text-text-muted">Run</th>
						<th class="px-3 py-2 text-xs font-medium text-text-muted">Type</th>
						<th class="px-3 py-2 text-xs font-medium text-text-muted">Target</th>
						<th class="px-3 py-2 text-right text-xs font-medium text-text-muted">Policy violation</th>
						<th class="px-3 py-2 text-right text-xs font-medium text-text-muted">Total</th>
					</tr>
				</thead>
				<tbody>
					{#each allRuns as run}
						{@const qRun = run.prompt}
						{@const aRun = run.audit}
						{@const hasChildren = Boolean(qRun && aRun)}
						{@const isExpanded = expandedRunIds.has(run.run_id)}
						{@const violationRate = aggregateRunViolationRate(run)}
						<tr class="border-b border-border/50 transition-colors hover:bg-surface/50">
							<td class="px-3 py-2">
								<button
									onclick={() => toggleCompareRun(run.compare_run_id)}
									class="flex h-4 w-4 items-center justify-center rounded border transition-colors {!run.compare_run_id ? 'cursor-not-allowed border-text-muted/20 opacity-40' : run.compare_run_id && selectedCompareRuns.has(run.compare_run_id) ? 'border-interactive bg-interactive' : 'border-text-muted/40 hover:border-interactive/60'}"
									disabled={!run.compare_run_id}
									aria-label="Select run for comparison"
								>
									{#if run.compare_run_id && selectedCompareRuns.has(run.compare_run_id)}
										<svg class="h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M5 13l4 4L19 7"/></svg>
									{/if}
								</button>
							</td>
							<td class="px-3 py-2">
								<div class="flex items-center gap-1.5">
									{#if hasChildren}
										<button class="flex h-4 w-4 items-center justify-center rounded text-text-muted transition-colors hover:text-text" onclick={() => toggleRunExpanded(run.run_id)} aria-expanded={isExpanded} aria-label={isExpanded ? 'Collapse run details' : 'Expand run details'}>
											<svg class="h-3 w-3 transition-transform duration-150 {isExpanded ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
										</button>
									{/if}
									<a href="/suite/{data.suite_id}/{run.prompt_run_id ?? run.audit_run_id ?? run.run_id}" class="text-sm font-medium text-interactive hover:underline">{run.run_id}</a>
								</div>
							</td>
							<td class="px-3 py-2 text-xs text-text-muted">
								{#if !hasChildren}{qRun ? 'Single-turn prompts' : 'Multi-turn scenarios'}{:else}<span>{[qRun ? 'Prompts' : '', aRun ? 'Scenarios' : ''].filter(Boolean).join(' + ')}</span>{/if}
							</td>
							<td class="px-3 py-2 font-mono text-xs text-text-muted">{runTarget(run)}</td>
							<td class="px-3 py-2 text-right">
								{#if violationRate !== null}
									<span class="font-mono text-xs font-semibold {metricRateClass(violationRate)}">{(violationRate * 100).toFixed(0)}%</span>
								{:else}
									<span class="text-xs text-text-muted">—</span>
								{/if}
							</td>
							<td class="px-3 py-2 text-right font-mono text-xs text-text-muted">{runTotal(run) || '—'}</td>
						</tr>
						{#if hasChildren && isExpanded && qRun}
							<tr class="border-b border-border/30 bg-surface/20 transition-colors hover:bg-surface/30">
								<td class="px-3 py-2"></td>
								<td class="px-3 py-2 pl-7"><a href="/suite/{data.suite_id}/{run.prompt_run_id ?? run.run_id}?tab=prompts" class="text-xs text-text-secondary hover:text-interactive hover:underline">Prompts</a></td>
								<td class="px-3 py-2 text-xs text-text-muted">Single-turn prompts</td>
								<td class="px-3 py-2 font-mono text-xs text-text-muted">{qRun.metrics?.target ?? '—'}</td>
								<td class="px-3 py-2 text-right">{#if qRun.metrics}<span class="font-mono text-xs font-semibold {metricRateClass(qRun.metrics.policy_violation_rate)}">{(qRun.metrics.policy_violation_rate * 100).toFixed(0)}%</span>{:else}<span class="text-xs text-text-muted">—</span>{/if}</td>
								<td class="px-3 py-2 text-right font-mono text-xs text-text-muted">{qRun.metrics?.total ?? '—'}</td>
							</tr>
						{/if}
						{#if hasChildren && isExpanded && aRun}
							<tr class="border-b border-border/30 bg-surface/20 transition-colors hover:bg-surface/30">
								<td class="px-3 py-2"></td>
								<td class="px-3 py-2 pl-7"><a href="/suite/{data.suite_id}/{run.audit_run_id ?? run.run_id}?tab=audit" class="text-xs text-text-secondary hover:text-interactive hover:underline">Scenarios</a></td>
								<td class="px-3 py-2 text-xs text-text-muted">Multi-turn scenarios</td>
								<td class="px-3 py-2 font-mono text-xs text-text-muted">{aRun.metrics?.target ?? '—'}</td>
								<td class="px-3 py-2 text-right">{#if aRun.metrics}<span class="font-mono text-xs font-semibold {metricRateClass(aRun.metrics.policy_violation_rate)}">{(aRun.metrics.policy_violation_rate * 100).toFixed(0)}%</span>{:else}<span class="text-xs text-text-muted">—</span>{/if}</td>
								<td class="px-3 py-2 text-right font-mono text-xs text-text-muted">{aRun.metrics?.total ?? '—'}</td>
							</tr>
						{/if}
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<div class="flex gap-5">
	<div class="min-w-0 flex flex-1 flex-col">
		<div class="mb-3 flex shrink-0 items-center gap-3 bg-bg pb-1">
			<h2 class="shrink-0 text-sm font-semibold text-text">Behavior categories</h2>
			<div class="flex-1 border-t border-border"></div>
			<span class="shrink-0 text-xs text-text-muted">{sortedBehaviors.length} categories</span>
		</div>

		{#if sortedBehaviors.length === 0}
			<div class="rounded-lg border border-border bg-surface px-6 py-10 text-center">
				<p class="text-sm text-text-secondary">No behavior categories generated yet.</p>
				<p class="mt-1 text-xs text-text-muted">Run the pipeline to generate behavior categories.</p>
			</div>
		{:else}
			<div class="overflow-hidden rounded-lg border border-border">
				<div class="grid items-center border-b border-border bg-surface px-4 py-2" style="grid-template-columns: 1fr 1fr 80px 80px 80px; column-gap: 12px">
					<span class="text-left text-xs font-medium text-text-muted">Behavior category</span>
					<span class="text-left text-xs font-medium text-text-muted">Policy status</span>
					<span class="text-right text-xs font-medium text-text-muted">Prompts</span>
					<span class="text-right text-xs font-medium text-text-muted">Scenarios</span>
					<span class="text-right text-xs font-medium text-text-muted">Evaluations</span>
				</div>
				{#each sortedBehaviors as behavior, idx}
					{@const pCount = promptCountsByBehavior.get(behavior.name) ?? 0}
					{@const sCount = scenarioCountsByBehavior.get(behavior.name) ?? 0}
					<div
						role="button"
						tabindex="0"
						class="grid cursor-pointer items-center px-4 py-3 text-left text-sm transition-colors {idx > 0 ? 'border-t border-border' : ''} {selectedBehavior === behavior.name ? 'border-l-2 border-l-interactive bg-interactive/5' : 'hover:bg-surface'}"
						style="grid-template-columns: 1fr 1fr 80px 80px 80px; column-gap: 12px"
						onclick={() => selectBehavior(behavior.name)}
						onkeydown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectBehavior(behavior.name); } }}
					>
						<span class="truncate font-medium text-text">{behavior.name}</span>
						<div>
							<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium {behavior.permissible ? 'bg-interactive/15 text-interactive' : 'bg-score-fail/15 text-score-fail'}">
								{behavior.permissible ? 'permissible' : 'not permissible'}
							</span>
						</div>
						<span class="text-right font-mono text-xs text-text-muted">{pCount}</span>
						<span class="text-right font-mono text-xs text-text-muted">{sCount}</span>
						<span class="text-right font-mono text-xs text-text-muted">{allRuns.length}</span>
					</div>
				{/each}
			</div>
		{/if}
	</div>

	{#if selectedBehavior && selectedBehaviorData}
		<div class="sticky top-16 max-h-[calc(100vh-120px)] w-[520px] shrink-0 self-start overflow-y-auto rounded-lg border border-border bg-surface">
			<div class="sticky top-0 z-10 border-b border-border bg-surface">
				<div class="px-5 py-3">
					<div class="flex items-start justify-between gap-3">
						<h3 class="min-w-0 text-[16px] font-semibold leading-snug text-text line-clamp-2">{selectedBehaviorData.name}</h3>
						<button onclick={closeSidePanel} class="rounded p-1 text-text-muted transition-colors hover:bg-surface-2 hover:text-text" title="Close panel">
							<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6 18L18 6M6 6l12 12"/></svg>
						</button>
					</div>
					<div class="mt-1.5">
						<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium {selectedBehaviorData.permissible ? 'bg-interactive/15 text-interactive' : 'bg-score-fail/15 text-score-fail'}">
							{selectedBehaviorData.permissible ? 'permissible' : 'not permissible'}
						</span>
					</div>
				</div>
				<div class="flex gap-0 px-5">
					{#each panelTabs as tab}
						<button
							class="border-b-2 px-3 py-2 text-xs font-medium transition-colors {panelTab === tab ? 'border-interactive text-text' : 'border-transparent text-text-muted hover:border-border hover:text-text-secondary'}"
							onclick={() => selectPanelTab(tab)}
						>
							{tab === 'definition' ? 'Definition' : tab === 'seeds' ? `Prompts ${selectedBehaviorData.promptCount} · Scenarios ${selectedBehaviorData.scenarioCount}` : `Evaluations${!behaviorEvalLoading && behaviorEvalSamples.length > 0 ? ` ${behaviorEvalSamples.length}` : ''}`}
						</button>
					{/each}
				</div>
			</div>

			<div class="px-5 py-4">
				{#if panelTab === 'definition'}
					<div class="space-y-5">
						<div>
							<h4 class="mb-2 text-xs font-medium text-text">Definition</h4>
							<div class="prose text-sm leading-relaxed text-text-secondary">{@html renderMarkdown(selectedBehaviorData.definition)}</div>
						</div>
						{#if selectedBehaviorData.examples?.length > 0}
							<div>
								<h4 class="mb-2 text-xs font-medium text-text">Examples</h4>
								<div class="space-y-1.5">
									{#each selectedBehaviorData.examples as example}
										<div class="border-l-2 border-border pl-3 text-sm leading-relaxed text-text-secondary">{example}</div>
									{/each}
								</div>
							</div>
						{/if}
					</div>
				{:else if panelTab === 'seeds'}
					<div class="space-y-6">
						<div>
							<div class="mb-2 flex items-center gap-2">
								<h4 class="text-xs font-medium text-text">Prompts</h4>
								<span class="ml-auto text-xs text-text-muted">{selectedBehaviorPrompts.length} prompts</span>
							</div>
							{#if selectedBehaviorPrompts.length === 0}
								<div class="py-4 text-center"><p class="text-sm text-text-secondary">No prompts for this category.</p></div>
							{:else}
								<div class="space-y-2">
									{#each selectedBehaviorPrompts as seed}
										<div class="rounded-lg border border-border bg-bg p-3">
											<div class="mb-1 text-sm font-medium text-text">{seed.title}</div>
											{#if seed.description}<p class="line-clamp-2 text-xs leading-relaxed text-text-muted">{seed.description}</p>{/if}
										</div>
									{/each}
								</div>
							{/if}
						</div>
						<div>
							<div class="mb-2 flex items-center gap-2">
								<h4 class="text-xs font-medium text-text">Scenarios</h4>
								<span class="ml-auto text-xs text-text-muted">{selectedBehaviorScenarios.length} scenarios</span>
							</div>
							{#if selectedBehaviorScenarios.length === 0}
								<div class="py-4 text-center"><p class="text-sm text-text-secondary">No scenarios for this category.</p></div>
							{:else}
								<div class="space-y-2">
									{#each selectedBehaviorScenarios as seed}
										<div class="rounded-lg border border-border bg-bg p-3">
											<div class="mb-1 text-sm font-medium text-text">{seed.title}</div>
											{#if seed.description}<p class="line-clamp-2 text-xs leading-relaxed text-text-muted">{seed.description}</p>{/if}
										</div>
									{/each}
								</div>
							{/if}
						</div>
					</div>
				{:else}
					<div class="space-y-3">
						<div class="mb-2 flex items-center gap-2">
							<h4 class="text-xs font-medium text-text">Evaluation results</h4>
							<span class="ml-auto text-xs text-text-muted">{behaviorEvalSamples.length} result{behaviorEvalSamples.length !== 1 ? 's' : ''}</span>
						</div>
						{#if behaviorEvalLoading}
							<div class="py-8 text-center">
								<div class="inline-block h-5 w-5 animate-spin rounded-full border-2 border-text-muted border-t-interactive"></div>
								<p class="mt-2 text-xs text-text-muted">Loading results...</p>
							</div>
						{:else if behaviorEvalError}
							<div class="py-6 text-center"><p class="text-sm text-score-fail">{behaviorEvalError}</p></div>
						{:else if behaviorEvalSamples.length === 0}
							<div class="py-8 text-center"><p class="text-sm text-text-secondary">No evaluation results for this category.</p></div>
						{:else}
							{#each behaviorEvalSamples as sample, idx}
								{@const status = sampleComplianceStatus(sample)}
								<button class="w-full cursor-pointer rounded-lg border border-border bg-bg p-3 text-left transition-colors hover:border-interactive/40 hover:bg-surface" onclick={() => openEvalDrawer(sample, idx)}>
									<div class="min-w-0 flex-1 space-y-2">
										<div><span class="text-[10px] font-medium text-text-muted">User prompt</span><p class="line-clamp-2 text-sm leading-snug text-text">{sample.prompt}</p></div>
										{#if sample.response}<div><span class="text-[10px] font-medium text-text-muted">Target response</span><p class="line-clamp-2 text-sm leading-snug text-text">{sample.response}</p></div>{/if}
										<span class="text-xs font-medium {status === 'flagged' ? 'text-score-fail' : status === 'compliant' ? 'text-score-pass' : status === 'error' ? 'text-yellow-500' : 'text-text-muted'}">
											{status === 'flagged' ? 'Policy violation' : status === 'compliant' ? 'Pass' : status === 'error' ? 'Judge failed' : 'Pending'}
										</span>
									</div>
								</button>
							{/each}
						{/if}
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>

{#if drawerLoading}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
		<div class="rounded-lg border border-border bg-surface p-6 text-center">
			<div class="inline-block h-6 w-6 animate-spin rounded-full border-2 border-text-muted border-t-interactive"></div>
			<p class="mt-2 text-sm text-text-muted">Loading conversation...</p>
		</div>
	</div>
{/if}

{#if drawerItem}
	<ResultDrawer
		item={drawerItem}
		metricNames={metricNames}
		primaryMetric={primaryMetric}
		requiredBaseMetrics={requiredBaseMetrics}
		navIdx={drawerNavIdx}
		navTotal={behaviorEvalSamples.length}
		onClose={closeDrawer}
		onPrev={() => navigateDrawer(-1)}
		onNext={() => navigateDrawer(1)}
	/>
{/if}
