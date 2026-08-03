<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import PrimerDropdown from '$lib/PrimerDropdown.svelte';
	import InfoTooltip from '$lib/components/InfoTooltip.svelte';
	import ExpandableText from '$lib/ExpandableText.svelte';
	import { judgeDimensionLabel } from '$lib/labels.js';
	import { renderMarkdown } from '$lib/markdown.js';
	import { mergeRunLists, normalizePromptSeeds, normalizeScenarioSeeds, type CombinedRunEntry } from '$lib/suite-view.js';
	import type { DimensionDef } from '$lib/types.js';
	import type { SuiteHeavyData } from '$lib/server/data.js';
	import { fade } from 'svelte/transition';
	import { page } from '$app/state';
	import { goto, invalidateAll } from '$app/navigation';

	let { data } = $props();

	let descExpanded = $state(false);
	let metaOpen = $state(false);
	let selectedBehavior = $state<string | null>(null);
	let behaviorSearch = $state('');
	let behaviorSort = $state<'permissible' | 'not_permissible'>('permissible');
	let selectedCompareRuns = $state<Set<string>>(new Set());
	let expandedRunIds = $state<Set<string>>(new Set());
	let heavyData = $state<SuiteHeavyData | null>(null);
	let heavyError = $state<string | null>(null);
	let heavyPending = $derived(heavyData === null && heavyError === null);
	let showSkeleton = $state(false);

	type SuiteStage = 'taxonomy' | 'test_set' | 'results';

	// --- Suite stages: taxonomy, test set, results ---
	let activeStage = $derived(
		(['test_set', 'results'].includes(page.url.searchParams.get('stage') ?? '')
			? page.url.searchParams.get('stage')
			: 'taxonomy') as SuiteStage
	);

	function setActiveStage(stage: SuiteStage) {
		if (stage === activeStage) return;
		const url = new URL(page.url);
		if (stage === 'taxonomy') url.searchParams.delete('stage');
		else url.searchParams.set('stage', stage);
		goto(url.toString(), { replaceState: true, noScroll: true, keepFocus: true });
	}

	// --- Per-category taxonomy editing ---
	// Each behavior category is edited inline from its own side panel; there is
	// no global edit mode. Only the selected category's definition and
	// permissible flag are mutated, leaving examples/metadata/order untouched.
	let editingCategory = $state<string | null>(null);
	let catSaving = $state(false);
	let catSaveError = $state<string | null>(null);
	let catDraftDef = $state('');
	let catDraftPermissible = $state(false);
	let catDraftExamples = $state<string[]>([]);

	function startCategoryEdit() {
		if (!selectedBehaviorData) return;
		catDraftDef = selectedBehaviorData.definition ?? '';
		catDraftPermissible = Boolean(selectedBehaviorData.permissible);
		catDraftExamples = [...(selectedBehaviorData.examples ?? [])];
		catSaveError = null;
		editingCategory = selectedBehaviorData.name;
	}

	function addDraftExample() {
		catDraftExamples = [...catDraftExamples, ''];
	}

	function removeDraftExample(index: number) {
		catDraftExamples = catDraftExamples.filter((_, i) => i !== index);
	}

	function cancelCategoryEdit() {
		editingCategory = null;
		catSaving = false;
		catSaveError = null;
	}

	async function saveCategoryEdit() {
		const categoryName = editingCategory;
		if (catSaving || !categoryName) return;
		catSaving = true;
		catSaveError = null;
		try {
			// Deep-clone the full taxonomy and mutate only the edited category so
			// metadata and node order are preserved exactly.
			const taxonomy = JSON.parse(JSON.stringify(data.taxonomy ?? {}));
			const target = (taxonomy.behavior_categories ?? []).find(
				(cat: { name?: string }) => cat.name === categoryName
			);
			if (!target) throw new Error('Category not found in taxonomy.');
			target.definition = catDraftDef;
			target.permissible = catDraftPermissible;
			// Drop blank examples so empty rows aren't persisted.
			target.examples = catDraftExamples.map((ex) => ex.trim()).filter((ex) => ex.length > 0);
			const res = await fetch('/api/taxonomy', {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ suite_id: data.suite_id, taxonomy })
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({}) as { error?: string });
				throw new Error(body.error ?? `Save failed (${res.status})`);
			}
			await invalidateAll();
			// Keep the panel open on the same category; just exit edit mode.
			editingCategory = null;
		} catch (err) {
			catSaveError = err instanceof Error ? err.message : String(err);
		} finally {
			catSaving = false;
		}
	}

	// --- Per-seed (test prompt / scenario) editing ---
	// Each prompt or scenario card can be edited inline. Only the seed's title
	// and description are mutated; the row is persisted back to test_set.jsonl
	// via PUT /api/seeds, keyed by its stable test_case_id.
	let editingSeedId = $state<string | null>(null);
	let seedSaving = $state(false);
	let seedSaveError = $state<string | null>(null);
	let seedDraftTitle = $state('');
	let seedDraftDescription = $state('');
	let testSetTab = $state<'prompt' | 'scenario'>('prompt');
	let testSetSearch = $state('');
	let selectedTestSetSeedId = $state<string | null>(null);

	function startSeedEdit(seed: { id: string; title: string; description: string }) {
		seedDraftTitle = seed.title ?? '';
		seedDraftDescription = seed.description ?? '';
		seedSaveError = null;
		editingSeedId = seed.id;
	}

	function cancelSeedEdit() {
		editingSeedId = null;
		seedSaving = false;
		seedSaveError = null;
	}

	function setTestSetTab(tab: 'prompt' | 'scenario') {
		if (testSetTab === tab) return;
		cancelSeedEdit();
		selectedTestSetSeedId = null;
		testSetTab = tab;
	}

	async function saveSeedEdit() {
		const testCaseId = editingSeedId;
		if (seedSaving || !testCaseId) return;
		seedSaving = true;
		seedSaveError = null;
		try {
			const res = await fetch('/api/seeds', {
				method: 'PUT',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({
					suite_id: data.suite_id,
					test_case_id: testCaseId,
					title: seedDraftTitle.trim(),
					description: seedDraftDescription
				})
			});
			if (!res.ok) {
				const body = await res.json().catch(() => ({}) as { error?: string });
				throw new Error(body.error ?? `Save failed (${res.status})`);
			}
			await invalidateAll();
			editingSeedId = null;
		} catch (err) {
			seedSaveError = err instanceof Error ? err.message : String(err);
		} finally {
			seedSaving = false;
		}
	}

	// --- Run evaluation ---
	// Opens the New evaluation wizard pre-filled for this suite, landing on the
	// Summary & submit step so the operator can review before submitting.
	function runEvaluation() {
		void goto(`/new?suite=${encodeURIComponent(data.suite_id)}`);
	}

	$effect(() => {
		const promise = data.streamed?.heavy;
		if (!promise) return;
		let cancelled = false;
		heavyData = null;
		heavyError = null;
		showSkeleton = false;
		const skeletonTimer = setTimeout(() => {
			if (!cancelled && heavyData === null && heavyError === null) {
				showSkeleton = true;
			}
		}, 180);
		promise.then(
			(result) => {
				if (!cancelled) heavyData = result;
			},
			(err) => {
				if (!cancelled) heavyError = err instanceof Error ? err.message : String(err);
			}
		);
		return () => {
			cancelled = true;
			clearTimeout(skeletonTimer);
		};
	});

	// Union of (a) globally-declared dim defs and (b) dim names actually present in run metrics.
	// (b) covers custom per-suite dims declared inline in eval.yaml judge.dimensions which never
	// reach loadDimensions() (that only reads BUILT_IN_DIMENSIONS + the global
	// examples/eval-definitions/judge_dimensions.yaml file). Mirrors the compare view's
	// loadComparePageData which derives data.allMetrics from Object.keys(summary.dimensions).
	let allDimNames = $derived.by(() => {
		const names = new Set<string>();
		for (const name of Object.keys((data.dimensionDefs ?? {}) as Record<string, DimensionDef>)) {
			names.add(name);
		}
		for (const r of allRuns) {
			for (const name of Object.keys(r.prompt?.metrics?.dimensions ?? {})) names.add(name);
			for (const name of Object.keys(r.audit?.metrics?.dimensions ?? {})) names.add(name);
		}
		return Array.from(names);
	});
	let dimNames = $derived(allDimNames);
	function dimColumnLabel(name: string): string {
		const spaced = name.replace(/_/g, ' ');
		return spaced.charAt(0).toUpperCase() + spaced.slice(1);
	}
	let visibleDimNames = $derived(
		dimNames.filter((name) =>
			allRuns.some((r) => aggregateRunDimensionRate(r, name) !== null)
		)
	);

	// Per-column dim selection for the evaluation-results table. Each of the
	// MAX_METRIC_COLS slots holds either a dim name (must currently be populated)
	// or null when the user hasn't picked a dim for that slot. State is sourced
	// from the URL search param ``metrics`` so links are shareable and reloads
	// preserve the user's column choice. Default = first MAX_METRIC_COLS entries
	// of visibleDimNames, preserving the existing ordering.
	const MAX_METRIC_COLS = 3;
	let selectedMetricCols = $derived.by<(string | null)[]>(() => {
		const populated = visibleDimNames;
		const numCols = Math.min(MAX_METRIC_COLS, populated.length);
		if (numCols === 0) return [];
		const raw = page.url.searchParams.get('metrics');
		const result: (string | null)[] = new Array(numCols).fill(null);
		const used = new Set<string>();
		if (raw) {
			const parts = raw.split(',').map((s) => s.trim());
			for (let i = 0; i < numCols && i < parts.length; i++) {
				const name = parts[i];
				if (name && populated.includes(name) && !used.has(name)) {
					result[i] = name;
					used.add(name);
				}
			}
		}
		// Fill empty slots with the first unused populated dim (preserves default ordering).
		for (let i = 0; i < numCols; i++) {
			if (result[i] !== null) continue;
			const fallback = populated.find((n) => !used.has(n));
			if (!fallback) break;
			result[i] = fallback;
			used.add(fallback);
		}
		return result;
	});

	function setMetricCol(colIdx: number, dimName: string) {
		const next = [...selectedMetricCols];
		if (colIdx < 0 || colIdx >= next.length) return;
		// If the chosen dim is already used in another slot, swap them so we never
		// end up with the same dim in two columns.
		const existingIdx = next.findIndex((n, i) => n === dimName && i !== colIdx);
		if (existingIdx >= 0) next[existingIdx] = next[colIdx];
		next[colIdx] = dimName;
		const url = new URL(page.url);
		url.searchParams.set('metrics', next.filter((n): n is string => Boolean(n)).join(','));
		goto(url.toString(), { replaceState: true, noScroll: true, keepFocus: true });
	}
	let sortedBehaviors = $derived(data.taxonomy?.behavior_categories ?? []);
	let promptSeedItems = $derived(normalizePromptSeeds(data.promptSeeds));
	let scenarioSeedItems = $derived(normalizeScenarioSeeds(data.scenarioSeeds));
	let testSetCount = $derived(promptSeedItems.length + scenarioSeedItems.length);
	let activeTestSetItems = $derived(testSetTab === 'prompt' ? promptSeedItems : scenarioSeedItems);
	let filteredTestSetItems = $derived.by(() => {
		const q = testSetSearch.trim().toLowerCase();
		if (!q) return activeTestSetItems;
		return activeTestSetItems.filter((seed) => {
			const dimText = Object.values(seed.dimensions ?? {}).join(' ').toLowerCase();
			return (
				seed.title.toLowerCase().includes(q) ||
				seed.description.toLowerCase().includes(q) ||
				seed.behavior.toLowerCase().includes(q) ||
				dimText.includes(q)
			);
		});
	});
	let selectedTestSetSeed = $derived.by(() => {
		if (!selectedTestSetSeedId) return null;
		return activeTestSetItems.find((seed) => seed.id === selectedTestSetSeedId) ?? null;
	});
	let allRuns = $derived(mergeRunLists(heavyData?.runs ?? [], heavyData?.auditRuns ?? []));

	// Suite-level CSV export menu
	let csvMenuOpen = $state(false);
	let csvExportItems = $derived([
		...(sortedBehaviors.length > 0 ? [{ label: 'Taxonomy', type: 'taxonomy' }] : []),
		...(promptSeedItems.length > 0 ? [{ label: 'Test set', type: 'test_set' }] : []),
		...(scenarioSeedItems.length > 0 ? [{ label: 'Scenario test set', type: 'scenario_test_set' }] : []),
		...(allRuns.some((r) => r.prompt?.has_judged) ? [{ label: 'All results', type: 'all_results' }] : []),
		...(allRuns.some((r) => r.audit?.has_scores) ? [{ label: 'All scenario scores', type: 'all_audit_scores' }] : [])
	]);

	function downloadCsv(type: string) {
		csvMenuOpen = false;
		window.open(`/api/csv/${encodeURIComponent(data.suite_id)}?type=${type}`, '_blank');
	}
	let conceptName = $derived(data.taxonomy?.behavior?.name ?? data.taxonomy?.risk?.name ?? data.suite_id);
	let conceptDef = $derived(data.taxonomy?.behavior?.definition ?? data.taxonomy?.risk?.definition ?? '');
	let summaryItemCount = $derived(Array.isArray(data.systematization?.summary_items) ? data.systematization.summary_items.length : 0);
	let systematizationMode = $derived(systematizationModeFor(data.systematization));
	let hasSystematization = $derived(Boolean(data.systematization));
	let systematizationText = $derived(
		typeof data.systematization?.systematization === 'string' ? data.systematization.systematization : ''
	);
	let canEdit = $derived(Boolean(data.editEnabled));
	let canCompare = $derived(selectedCompareRuns.size >= 2);
	const MAX_COMPARE_RUNS = 3;
	const BEHAVIOR_TABLE_COLUMNS = 'minmax(0,1fr) 140px 24px';
	const BEHAVIOR_SORT_OPTIONS = [
		{ value: 'permissible', label: 'Permissible' },
		{ value: 'not_permissible', label: 'Not Permissible' }
	];

	let visibleBehaviors = $derived.by(() => {
		const q = behaviorSearch.trim().toLowerCase();
		let items = data.taxonomy?.behavior_categories ?? [];
		if (q) {
			items = items.filter((behavior) => {
				const name = (behavior.name ?? '').toLowerCase();
				const definition = (behavior.definition ?? '').toLowerCase();
				return name.includes(q) || definition.includes(q);
			});
		}
		return [...items].sort((a, b) => {
			if (a.permissible === b.permissible) return a.name.localeCompare(b.name);
			return behaviorSort === 'permissible'
				? (a.permissible ? -1 : 1)
				: (a.permissible ? 1 : -1);
		});
	});

	let selectedBehaviorData = $derived.by(() => {
		if (!selectedBehavior) return null;
		const idx = sortedBehaviors.findIndex((behavior) => behavior.name === selectedBehavior);
		if (idx < 0) return null;
		const behavior = sortedBehaviors[idx];
		return {
			...behavior,
			idx
		};
	});

	$effect(() => {
		if (allRuns.length <= 3) expandedRunIds = new Set(allRuns.map((run) => run.run_id));
		else expandedRunIds = new Set();
	});

	function metricLabel(metric: string): string {
		const label = judgeDimensionLabel(metric);
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
		else {
			if (next.size >= MAX_COMPARE_RUNS) return;
			next.add(compareId);
		}
		selectedCompareRuns = next;
	}

	function openComparePage() {
		if (!canCompare) return;
		window.location.href = `/suite/${data.suite_id}/compare?runs=${[...selectedCompareRuns].join(',')}`;
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
		const promptRate = run.prompt?.metrics?.policy_violation_rate;
		const auditRate = run.audit?.metrics?.policy_violation_rate;
		const applicableTotal = (promptRate == null ? 0 : promptTotal) + (auditRate == null ? 0 : auditTotal);
		if (applicableTotal === 0) return null;
		const promptViolations = promptRate == null ? 0 : promptTotal * promptRate;
		const auditViolations = auditRate == null ? 0 : auditTotal * auditRate;
		return (promptViolations + auditViolations) / applicableTotal;
	}

	function aggregateRunOverrefusalRate(run: CombinedRunEntry): number | null {
		const promptTotal = run.prompt?.metrics?.total ?? 0;
		const auditTotal = run.audit?.metrics?.total ?? 0;
		const total = promptTotal + auditTotal;
		if (total === 0) return null;
		const promptRate = run.prompt?.metrics?.overrefusal_rate;
		const auditRate = run.audit?.metrics?.overrefusal_rate;
		const applicableTotal = (promptRate == null ? 0 : promptTotal) + (auditRate == null ? 0 : auditTotal);
		if (applicableTotal === 0) return null;
		const promptVal = promptRate == null ? 0 : promptTotal * promptRate;
		const auditVal = auditRate == null ? 0 : auditTotal * auditRate;
		return (promptVal + auditVal) / applicableTotal;
	}

	function aggregateRunDimensionRate(run: CombinedRunEntry, dimension: string): number | null {
		const promptDim = run.prompt?.metrics?.dimensions?.[dimension];
		const auditDim = run.audit?.metrics?.dimensions?.[dimension];
		const promptCount = promptDim?.count ?? 0;
		const auditCount = auditDim?.count ?? 0;
		const total = promptCount + auditCount;
		if (total === 0) return null;
		const flagged = (promptDim?.flagged_count ?? 0) + (auditDim?.flagged_count ?? 0);
		return flagged / total;
	}

	function runTotal(run: CombinedRunEntry): number {
		return (run.prompt?.metrics?.total ?? 0) + (run.audit?.metrics?.total ?? 0);
	}

	function runTarget(run: CombinedRunEntry): string {
		return run.prompt?.metrics?.target ?? run.audit?.metrics?.target ?? '—';
	}

	function selectBehavior(name: string) {
		cancelCategoryEdit();
		cancelSeedEdit();
		if (selectedBehavior === name) {
			selectedBehavior = null;
			return;
		}
		selectedBehavior = name;
	}

	function closeSidePanel() {
		cancelCategoryEdit();
		cancelSeedEdit();
		selectedBehavior = null;
	}
</script>

<div class="mb-6">
	<nav aria-label="Breadcrumb">
		<ol class="Breadcrumb">
			<li class="Breadcrumb-item"><a href="/">Evaluation suites</a></li>
			<li class="Breadcrumb-item" aria-current="page">{conceptName}</li>
		</ol>
	</nav>
	<div class="mt-5 flex w-full items-start gap-4">
		<div class="min-w-0 flex-1">
			<div class="text-[12px] font-medium text-text-muted">Behavior name</div>
			<h1 class="break-words text-2xl font-semibold leading-tight text-text" style="margin-top:2px;" title={conceptName}>{conceptName}</h1>
			<div class="mt-1.5 flex flex-wrap items-center gap-2">
				<span class="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 font-mono text-xs text-text-muted">
					<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z"/></svg>
					{data.suite_id}
				</span>
				<span class="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-xs text-text-muted">
					<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
					Created {data.suite?.created_at ? new Date(data.suite.created_at).toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' }) : '—'}
				</span>
				{#if hasSystematization}
					<button class="inline-flex items-center gap-1.5 rounded-full! bg-surface-2 px-2.5 py-1 text-xs text-text-muted transition-colors hover:text-text-secondary" onclick={() => metaOpen = !metaOpen}>
						<svg class="h-3 w-3 transition-transform" style={metaOpen ? 'transform:rotate(180deg)' : ''} fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>
						{metaOpen ? 'hide details' : 'details'}
					</button>
				{/if}
			</div>
			{#if conceptDef}
				<div class="mt-3 text-[12px] font-medium text-text-muted">Behavior description</div>
				{#if descExpanded}
					<p class="text-sm leading-relaxed text-text" style="margin-top:2px;">{conceptDef} <button type="button" class="text-interactive hover:text-interactive-hover hover:underline" onclick={() => descExpanded = false}>Show less</button></p>
				{:else if conceptDef.length > 260}
					<p class="text-sm leading-relaxed text-text" style="margin-top:2px;">{conceptDef.slice(0, 260).trimEnd()}… <button type="button" class="text-interactive hover:text-interactive-hover hover:underline" onclick={() => descExpanded = true}>Show more</button></p>
				{:else}
					<p class="text-sm leading-relaxed text-text" style="margin-top:2px;">{conceptDef}</p>
				{/if}
			{/if}
			<div class="mt-3 flex flex-wrap items-center gap-2">
				<span class="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-xs text-text-muted">
					{sortedBehaviors.length} behavior categories
				</span>
				<span class="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-xs text-text-muted">
					{promptSeedItems.length + scenarioSeedItems.length} evaluation test sets
				</span>
				{#if activeStage === 'results'}
					<span class="inline-flex items-center gap-1.5 rounded-full bg-surface-2 px-2.5 py-1 text-xs text-text-muted">
						{allRuns.length} evaluation results
					</span>
				{/if}
			</div>
		</div>
		{#if promptSeedItems.length + scenarioSeedItems.length > 0}
			<a
				href="/api/download/{data.suite_id}/test_set.jsonl"
				download="test_set.jsonl"
				class="btn btn-primary shrink-0 no-underline whitespace-nowrap"
				style="display:inline-flex; align-items:center; gap:0.5rem;"
			>
				<svg class="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5m0 0l5-5m-5 5V4"/></svg>
				<span>Export evaluation set</span>
			</a>
		{/if}
		{#if csvExportItems.length > 0}
			<div class="relative shrink-0">
				<button
					type="button"
					class="btn whitespace-nowrap"
					style="display:inline-flex; align-items:center; gap:0.5rem;"
					aria-haspopup="menu"
					aria-expanded={csvMenuOpen}
					onclick={() => (csvMenuOpen = !csvMenuOpen)}
					title="Export CSV"
				>
					<svg class="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 10v6m0 0l-3-3m3 3l3-3M3 17V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"/></svg>
					<span>Export CSV</span>
					<svg class="h-3 w-3 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"/></svg>
				</button>
				{#if csvMenuOpen}
					<button type="button" class="fixed inset-0 z-10 cursor-default" aria-label="Close export menu" onclick={() => (csvMenuOpen = false)}></button>
					<div class="absolute right-0 top-full z-20 mt-1 min-w-[180px] rounded-lg border border-border bg-surface py-1 shadow-lg" role="menu">
						{#each csvExportItems as csvItem}
							<button
								type="button"
								role="menuitem"
								class="block w-full px-3 py-1.5 text-left text-xs text-text-secondary transition-colors hover:bg-surface-2 hover:text-text"
								onclick={() => downloadCsv(csvItem.type)}
							>{csvItem.label}</button>
						{/each}
					</div>
				{/if}
			</div>
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

<!-- Stage tabs: taxonomy, test set, results -->
<div class="mb-5 border-b border-border">
	<div class="SegmentedControl" role="tablist" aria-label="Suite stage">
		<button
			type="button"
			role="tab"
			aria-selected={activeStage === 'taxonomy'}
			class="SegmentedControl-item"
			class:SegmentedControl-item--selected={activeStage === 'taxonomy'}
			onclick={() => setActiveStage('taxonomy')}
		>
			<span class="SegmentedControl-content">Taxonomy</span>
		</button>
		<button
			type="button"
			role="tab"
			aria-selected={activeStage === 'test_set'}
			class="SegmentedControl-item"
			class:SegmentedControl-item--selected={activeStage === 'test_set'}
			onclick={() => setActiveStage('test_set')}
		>
			<span class="SegmentedControl-content">Test set</span>
		</button>
		<button
			type="button"
			role="tab"
			aria-selected={activeStage === 'results'}
			class="SegmentedControl-item"
			class:SegmentedControl-item--selected={activeStage === 'results'}
			onclick={() => setActiveStage('results')}
		>
			<span class="SegmentedControl-content">Results</span>
		</button>
	</div>
</div>

{#if activeStage === 'test_set'}
<div class="mb-6">
	<div class="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-2">
		<div class="min-w-0">
			<h2 class="text-lg font-semibold text-text">Test set</h2>
			<p class="mt-1 text-sm leading-5 text-text-muted">Review and edit the direct prompts and multi-turn scenarios generated from the taxonomy.</p>
		</div>
		<div class="flex shrink-0 items-center gap-2">
			<span class="text-xs text-text-muted">{testSetCount} test {testSetCount === 1 ? 'case' : 'cases'}</span>
			<button
				type="button"
				class="btn btn-primary btn-small"
				onclick={runEvaluation}
				style="display:inline-flex; align-items:center; gap:0.4rem;"
			>
				<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.752 11.168l-5.197-3.027A1 1 0 008 9.027v5.946a1 1 0 001.555.832l5.197-3.027a1 1 0 000-1.638z"/></svg>
				Run evaluation
			</button>
		</div>
	</div>

	{#if testSetCount === 0}
		<div class="rounded-lg border border-border bg-surface px-6 py-10 text-center">
			<p class="text-sm text-text-secondary">No test set generated yet.</p>
			<p class="mt-1 text-xs text-text-muted">Run the pipeline to generate direct prompts and scenarios.</p>
		</div>
	{:else}
		<div class="mb-4 flex flex-wrap items-center justify-between gap-3">
			<div class="SegmentedControl" role="tablist" aria-label="Test set type">
				<button
					type="button"
					role="tab"
					aria-selected={testSetTab === 'prompt'}
					class="SegmentedControl-item"
					class:SegmentedControl-item--selected={testSetTab === 'prompt'}
					onclick={() => setTestSetTab('prompt')}
				>
					<span class="SegmentedControl-content">Prompts {promptSeedItems.length}</span>
				</button>
				<button
					type="button"
					role="tab"
					aria-selected={testSetTab === 'scenario'}
					class="SegmentedControl-item"
					class:SegmentedControl-item--selected={testSetTab === 'scenario'}
					onclick={() => setTestSetTab('scenario')}
				>
					<span class="SegmentedControl-content">Scenarios {scenarioSeedItems.length}</span>
				</button>
			</div>
			<span class="text-xs text-text-muted">{filteredTestSetItems.length} of {activeTestSetItems.length} {testSetTab === 'prompt' ? 'prompts' : 'scenarios'}</span>
		</div>

		<div class="flex gap-5">
			<div class="min-w-0 flex flex-1 flex-col">
				<div class="mb-2 flex items-center justify-between gap-3">
					<div class="relative" style="flex: 1 1 auto; max-width: 480px; min-width: 240px;">
						<label for="test-set-search" class="sr-only">Search test set</label>
						<svg class="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor">
							<path d="M10.68 11.74a6 6 0 0 1-7.922-8.982 6 6 0 0 1 8.982 7.922l3.04 3.04a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215ZM11.5 7a4.499 4.499 0 1 0-8.997 0A4.499 4.499 0 0 0 11.5 7Z" />
						</svg>
						<input
							id="test-set-search"
							type="text"
							placeholder="Search test set…"
							bind:value={testSetSearch}
							class="form-control w-full"
							style="padding-left: 2rem;"
						/>
					</div>
				</div>

				{#if activeTestSetItems.length === 0}
					<div class="rounded-lg border border-border bg-surface px-6 py-10 text-center">
						<p class="text-sm text-text-secondary">No {testSetTab === 'prompt' ? 'direct prompts' : 'multi-turn scenarios'}.</p>
					</div>
				{:else if filteredTestSetItems.length === 0}
					<div class="rounded-lg border border-border bg-surface px-6 py-10 text-center">
						<p class="text-sm text-text-secondary">No matching {testSetTab === 'prompt' ? 'prompts' : 'scenarios'}.</p>
					</div>
				{:else}
					<div class="overflow-hidden rounded-lg border border-border">
						<div class="grid items-center border-b border-border bg-surface px-4 py-2" style="grid-template-columns: minmax(0,1fr) minmax(160px,240px) 120px 24px; column-gap: 12px">
							<span class="text-left text-xs font-medium text-text-muted">Test case</span>
							<span class="text-left text-xs font-medium text-text-muted">Behavior category</span>
							<span class="text-left text-xs font-medium text-text-muted">Dimensions</span>
							<span aria-hidden="true"></span>
						</div>
						{#each filteredTestSetItems as seed, idx (seed.id)}
							{@const dimEntries = Object.entries(seed.dimensions ?? {}).filter(([name]) => name !== 'behavior')}
							<div
								role="button"
								tabindex="0"
								class="grid cursor-pointer items-center px-4 py-3 text-left text-sm transition-colors {idx > 0 ? 'border-t border-border' : ''} {selectedTestSetSeedId === seed.id ? 'border-l-2 border-l-interactive bg-interactive/5' : 'hover:bg-surface'}"
								style="grid-template-columns: minmax(0,1fr) minmax(160px,240px) 120px 24px; column-gap: 12px"
								onclick={() => { cancelSeedEdit(); selectedTestSetSeedId = seed.id; }}
								onkeydown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); cancelSeedEdit(); selectedTestSetSeedId = seed.id; } }}
							>
								<span class="truncate font-medium text-text" title={seed.title}>{seed.title}</span>
								<span class="truncate text-xs text-text-muted" title={seed.behavior}>{seed.behavior}</span>
								<span class="text-left text-xs text-text-muted">{dimEntries.length}</span>
								<span class="flex justify-end text-text-muted">
									<svg class="h-4 w-4 transition-transform {selectedTestSetSeedId === seed.id ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
								</span>
							</div>
						{/each}
					</div>
				{/if}
			</div>

			{#if selectedTestSetSeed}
				<div class="sticky top-16 max-h-[calc(100vh-120px)] w-[520px] shrink-0 self-start overflow-y-auto [scrollbar-gutter:stable] rounded-lg border border-border bg-surface">
					<div class="sticky top-0 z-10 border-b border-border bg-surface px-5 py-3">
						<div class="flex items-start justify-between gap-3">
							<div class="min-w-0">
								<div class="mb-1 flex flex-wrap items-center gap-2">
									<span class="inline-flex rounded bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-text-muted">{selectedTestSetSeed.kind === 'prompt' ? 'Prompt' : 'Scenario'}</span>
									<span class="truncate text-xs text-text-muted" title={selectedTestSetSeed.behavior}>{selectedTestSetSeed.behavior}</span>
								</div>
								<h3 class="break-words text-[16px] font-semibold leading-snug text-text" title={selectedTestSetSeed.title}>{selectedTestSetSeed.title}</h3>
							</div>
							<button onclick={() => { cancelSeedEdit(); selectedTestSetSeedId = null; }} class="rounded p-1 text-text-muted transition-colors hover:bg-surface-2 hover:text-text" title="Close panel">
								<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6 18L18 6M6 6l12 12"/></svg>
							</button>
						</div>
					</div>
					<div class="space-y-5 px-5 py-4">
						{#if editingSeedId === selectedTestSetSeed.id}
							{#if seedSaveError}
								<div class="rounded-lg border border-score-fail/40 bg-score-fail/10 px-3 py-2 text-xs text-score-fail">{seedSaveError}</div>
							{/if}
							<div>
								<label class="mb-1 block text-xs font-medium text-text-muted" for="test-set-title-{selectedTestSetSeed.id}">Title</label>
								<input id="test-set-title-{selectedTestSetSeed.id}" class="form-control w-full text-sm" bind:value={seedDraftTitle} />
							</div>
							<div>
								<label class="mb-1 block text-xs font-medium text-text-muted" for="test-set-desc-{selectedTestSetSeed.id}">{selectedTestSetSeed.kind === 'prompt' ? 'Prompt' : 'Scenario'}</label>
								<textarea id="test-set-desc-{selectedTestSetSeed.id}" class="form-control w-full text-sm leading-relaxed" rows="8" bind:value={seedDraftDescription}></textarea>
							</div>
							<div class="flex items-center justify-end gap-2">
								<button class="btn btn-small" onclick={cancelSeedEdit} disabled={seedSaving}>Cancel</button>
								<button class="btn btn-primary btn-small" onclick={saveSeedEdit} disabled={seedSaving}>{seedSaving ? 'Saving…' : 'Save'}</button>
							</div>
						{:else}
							<div>
								<div class="mb-2 flex items-center justify-between gap-2">
									<h4 class="text-xs font-medium text-text">{selectedTestSetSeed.kind === 'prompt' ? 'Prompt' : 'Scenario'}</h4>
									{#if canEdit}
										<button class="btn btn-small shrink-0" onclick={() => startSeedEdit(selectedTestSetSeed)} style="display:inline-flex; align-items:center; gap:0.35rem;">
											<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
											Edit
										</button>
									{/if}
								</div>
								{#if selectedTestSetSeed.description}
									<div class="prose text-sm leading-relaxed text-text-secondary">{@html renderMarkdown(selectedTestSetSeed.description)}</div>
								{:else}
									<p class="text-sm text-text-muted">No description.</p>
								{/if}
							</div>
							{@const dimEntries = Object.entries(selectedTestSetSeed.dimensions ?? {}).filter(([name]) => name !== 'behavior')}
							{#if dimEntries.length > 0}
								<div>
									<h4 class="mb-2 text-xs font-medium text-text">Dimensions</h4>
									<div class="flex flex-wrap gap-1.5">
										{#each dimEntries as [name, value]}
											<span class="rounded bg-surface-2 px-2 py-0.5 text-[10px] text-text-muted" title={name}>{name}: {value}</span>
										{/each}
									</div>
								</div>
							{/if}
							{#if selectedTestSetSeed.system_prompt}
								<div>
									<h4 class="mb-2 text-xs font-medium text-text">System prompt</h4>
									<div class="rounded border border-border bg-bg p-3 text-xs leading-relaxed text-text-muted">{selectedTestSetSeed.system_prompt}</div>
								</div>
							{/if}
						{/if}
					</div>
				</div>
			{/if}
		</div>
	{/if}
</div>
{/if}

{#if activeStage === 'results'}
<div class="mb-6">
	<div class="mb-4 border-b border-border pb-2">
		<div class="flex items-center gap-3">
			<h2 class="min-w-0 flex-1 text-lg font-semibold text-text">Evaluation results</h2>
			<div class="flex shrink-0 items-center gap-2">
				{#if selectedCompareRuns.size > 0}<span class="text-xs text-text-muted">{selectedCompareRuns.size} selected</span>{/if}
				<span title={!canCompare ? `Select at least 2 runs to compare (up to ${MAX_COMPARE_RUNS}).` : 'Compare selected runs'}>
					<button
						class="btn btn-small"
						disabled={!canCompare}
						onclick={openComparePage}
					>
						Compare
					</button>
				</span>
				{#if selectedCompareRuns.size > 0}<button class="btn btn-invisible btn-small" onclick={() => selectedCompareRuns = new Set()}>Clear</button>{/if}
				<span class="text-xs text-text-muted">
					{#if showSkeleton}
						<span class="inline-block h-3 w-12 animate-pulse rounded bg-surface-2 align-middle"></span>
					{:else if heavyData}
						{allRuns.length} runs
					{/if}
				</span>
			</div>
		</div>
		<p class="mt-1 text-sm leading-5 text-text-muted">View all evaluation runs for this behavior category. Select up to {MAX_COMPARE_RUNS} runs, then click Compare.</p>
	</div>

	{#if heavyError}
		<div class="rounded-lg border border-border bg-surface px-6 py-8 text-center">
			<p class="text-sm text-score-fail">Failed to load run details: {heavyError}</p>
		</div>
	{:else if heavyData}
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
						<th class="px-3 py-2 text-xs font-medium text-text-muted">
							<span class="inline-flex items-center gap-1">Type
								<InfoTooltip direction="se" label="Direct prompts are single-turn requests. Multi-turn scenarios simulate longer user conversations against the target." />
							</span>
						</th>
						<th class="px-3 py-2 text-xs font-medium text-text-muted">Run date</th>
						<th class="px-3 py-2 text-xs font-medium text-text-muted">Run status</th>
						{#each selectedMetricCols as colDim, colIdx (colIdx)}
							<th class="w-32 px-3 py-2 text-left text-xs font-medium text-text-muted whitespace-nowrap">
								<PrimerDropdown
									ariaLabel={`Metric column ${colIdx + 1}`}
									selected={colDim ?? ''}
									options={visibleDimNames.map((optDim) => {
										const usedColIdx = selectedMetricCols.findIndex((n, i) => n === optDim && i !== colIdx);
										const isUsedElsewhere = usedColIdx >= 0;
										return {
											value: optDim,
											label: dimColumnLabel(optDim),
											disabled: isUsedElsewhere,
											secondaryLabel: isUsedElsewhere ? `in column ${usedColIdx + 1}` : undefined
										};
									})}
									onSelect={(value) => setMetricCol(colIdx, value)}
								/>
							</th>
						{/each}
						<th class="px-3 py-2 text-left text-xs font-medium text-text-muted">Total</th>
					</tr>
				</thead>
				<tbody>
					{#each allRuns as run}
						{@const qRun = run.prompt}
						{@const aRun = run.audit}
						{@const hasChildren = Boolean(qRun && aRun)}
						{@const isCompareSelected = Boolean(run.compare_run_id && selectedCompareRuns.has(run.compare_run_id))}
						{@const isCompareDisabled = !run.compare_run_id || (!isCompareSelected && selectedCompareRuns.size >= MAX_COMPARE_RUNS)}
						{@const isExpanded = expandedRunIds.has(run.run_id)}
						{@const runStartedAt = qRun?.manifest?.started_at ?? aRun?.manifest?.started_at ?? null}
						{@const runStatus = qRun?.manifest?.status ?? aRun?.manifest?.status ?? null}
						{@const runStatusLabel = runStatus === 'completed' ? 'complete' : runStatus === 'failed' ? 'failed' : runStatus === 'running' ? 'running' : 'incomplete'}
						{@const runStatusClass = runStatus === 'completed' ? 'bg-score-pass/15 text-score-pass' : runStatus === 'failed' ? 'bg-score-fail/15 text-score-fail' : runStatus === 'running' ? 'bg-interactive/15 text-interactive' : 'bg-surface-2 text-text-muted'}
						<tr class="border-b border-border/50 transition-colors hover:bg-surface/50">
							<td class="px-3 py-2">
								<button
									onclick={() => toggleCompareRun(run.compare_run_id)}
									class="flex h-4 w-4 items-center justify-center rounded border transition-colors {isCompareDisabled ? 'cursor-not-allowed border-text-muted/20 opacity-40' : isCompareSelected ? 'border-interactive bg-interactive' : 'border-text-muted/40 hover:border-interactive/60'}"
									disabled={isCompareDisabled}
									title={isCompareDisabled && selectedCompareRuns.size >= MAX_COMPARE_RUNS ? `You can compare up to ${MAX_COMPARE_RUNS} runs.` : 'Select run for comparison'}
									aria-label="Select run for comparison"
								>
									{#if isCompareSelected}
										<svg class="h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M5 13l4 4L19 7"/></svg>
									{/if}
								</button>
							</td>
							<td class="px-3 py-2">
								<div class="flex items-start gap-1.5">
									{#if hasChildren}
										<button class="mt-0.5 flex h-4 w-4 items-center justify-center rounded text-text-muted transition-colors hover:text-text" onclick={() => toggleRunExpanded(run.run_id)} aria-expanded={isExpanded} aria-label={isExpanded ? 'Collapse run details' : 'Expand run details'}>
											<svg class="h-3 w-3 transition-transform duration-150 {isExpanded ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
										</button>
									{/if}
									<div class="min-w-0 flex flex-col">
										<a href="/suite/{data.suite_id}/{run.prompt_run_id ?? run.audit_run_id ?? run.run_id}" class="text-sm font-medium text-interactive hover:underline">{run.run_id}</a>
										<span class="font-mono text-[10px] text-text-muted truncate" title={runTarget(run)}>{runTarget(run)}</span>
									</div>
								</div>
							</td>
							<td class="px-3 py-2 text-xs text-text-muted">
								{#if !hasChildren}{qRun ? 'Single-turn prompts' : 'Multi-turn scenarios'}{:else}<span>{[qRun ? 'Prompts' : '', aRun ? 'Scenarios' : ''].filter(Boolean).join(' + ')}</span>{/if}
							</td>
							<td class="px-3 py-2 text-xs text-text-muted">{runStartedAt ? new Date(runStartedAt).toLocaleDateString('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' }) : '—'}</td>
							<td class="px-3 py-2">
								<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium {runStatusClass}">{runStatusLabel}</span>
							</td>
							{#each selectedMetricCols as colDim, colIdx (colIdx)}
								{@const dimRate = colDim ? aggregateRunDimensionRate(run, colDim) : null}
								<td class="px-3 py-2 text-left">
									{#if dimRate !== null}
										<span class="text-xs font-semibold">{(dimRate * 100).toFixed(0)}%</span>
									{:else}
										<span class="text-xs text-text-muted">—</span>
									{/if}
								</td>
							{/each}
							<td class="px-3 py-2 text-left text-xs text-text-muted">{runTotal(run) || '—'}</td>
						</tr>
						{#if hasChildren && isExpanded && qRun}
							<tr class="border-b border-border/30 bg-surface/20 transition-colors hover:bg-surface/30">
								<td class="px-3 py-2"></td>
								<td class="px-3 py-2 pl-7"><a href="/suite/{data.suite_id}/{run.prompt_run_id ?? run.run_id}?tab=prompts" class="text-xs text-text-secondary hover:text-interactive hover:underline">Prompts</a></td>
								<td class="px-3 py-2 text-xs text-text-muted">Single-turn prompts</td>
								<td class="px-3 py-2"></td>
								<td class="px-3 py-2"></td>
								{#each selectedMetricCols as colDim, colIdx (colIdx)}
									{@const childDimRate = colDim ? (qRun.metrics?.dimensions?.[colDim]?.rate ?? null) : null}
									<td class="px-3 py-2 text-left">{#if childDimRate !== null && childDimRate !== undefined}<span class="text-xs font-semibold">{(childDimRate * 100).toFixed(0)}%</span>{:else}<span class="text-xs text-text-muted">—</span>{/if}</td>
								{/each}
								<td class="px-3 py-2 text-left text-xs text-text-muted">{qRun.metrics?.total ?? '—'}</td>
							</tr>
						{/if}
						{#if hasChildren && isExpanded && aRun}
							<tr class="border-b border-border/30 bg-surface/20 transition-colors hover:bg-surface/30">
								<td class="px-3 py-2"></td>
								<td class="px-3 py-2 pl-7"><a href="/suite/{data.suite_id}/{run.audit_run_id ?? run.run_id}?tab=audit" class="text-xs text-text-secondary hover:text-interactive hover:underline">Scenarios</a></td>
								<td class="px-3 py-2 text-xs text-text-muted">Multi-turn scenarios</td>
								<td class="px-3 py-2"></td>
								<td class="px-3 py-2"></td>
								{#each selectedMetricCols as colDim, colIdx (colIdx)}
									{@const childDimRate = colDim ? (aRun.metrics?.dimensions?.[colDim]?.rate ?? null) : null}
									<td class="px-3 py-2 text-left">{#if childDimRate !== null && childDimRate !== undefined}<span class="text-xs font-semibold">{(childDimRate * 100).toFixed(0)}%</span>{:else}<span class="text-xs text-text-muted">—</span>{/if}</td>
								{/each}
								<td class="px-3 py-2 text-left text-xs text-text-muted">{aRun.metrics?.total ?? '—'}</td>
							</tr>
						{/if}
					{/each}
				</tbody>
			</table>
		</div>
		{/if}
	{:else if showSkeleton}
		<div class="overflow-hidden rounded-lg border border-border" out:fade={{ duration: 100 }}>
			<div class="border-b border-border bg-surface px-3 py-2">
				<div class="h-3 w-32 animate-pulse rounded bg-surface-2"></div>
			</div>
			{#each Array(3) as _, idx}
				<div class="flex items-center gap-3 px-3 py-2.5 {idx > 0 ? 'border-t border-border/50' : ''}">
					<div class="h-3 w-3 animate-pulse rounded bg-surface-2"></div>
					<div class="h-3 w-32 animate-pulse rounded bg-surface-2"></div>
					<div class="h-3 w-24 animate-pulse rounded bg-surface-2"></div>
					<div class="h-3 w-20 animate-pulse rounded bg-surface-2"></div>
					<div class="ml-auto h-3 w-12 animate-pulse rounded bg-surface-2"></div>
					<div class="h-3 w-12 animate-pulse rounded bg-surface-2"></div>
					<div class="h-3 w-12 animate-pulse rounded bg-surface-2"></div>
				</div>
			{/each}
		</div>
	{/if}
</div>
{/if}

{#if activeStage === 'taxonomy'}
<div class="mb-5 flex flex-wrap items-center justify-between gap-3">
	<div class="min-w-0">
		<h2 class="text-lg font-semibold text-text">Taxonomy</h2>
		<p class="mt-1 text-sm text-text-muted">Inspect and edit behavior categories and policy status, then run an evaluation.</p>
	</div>
	<div class="flex shrink-0 items-center gap-2">
		<button
			type="button"
			class="btn btn-primary btn-small"
			onclick={runEvaluation}
			style="display:inline-flex; align-items:center; gap:0.4rem;"
		>
			<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M14.752 11.168l-5.197-3.027A1 1 0 008 9.027v5.946a1 1 0 001.555.832l5.197-3.027a1 1 0 000-1.638z"/></svg>
			Run evaluation
		</button>
	</div>
</div>

<div class="mb-4 border-b border-border pb-2">
	<div class="flex items-center gap-3">
		<h2 class="min-w-0 flex-1 text-lg font-semibold text-text">Behavior categories</h2>
		<span class="shrink-0 text-xs text-text-muted">{visibleBehaviors.length} of {sortedBehaviors.length} categories</span>
	</div>
	<p class="mt-1 text-sm leading-5 text-text-muted">Browse and edit behavior categories systematized for {conceptName}. Select a category to view or edit its definition and policy status.</p>
</div>

<div class="flex gap-5">
	<div class="min-w-0 flex flex-1 flex-col">
		{#if sortedBehaviors.length === 0}
			<div class="rounded-lg border border-border bg-surface px-6 py-10 text-center">
				<p class="text-sm text-text-secondary">No behavior categories generated yet.</p>
				<p class="mt-1 text-xs text-text-muted">Run the pipeline to generate behavior categories.</p>
			</div>
		{:else}
			<div class="mb-2 flex items-center justify-between gap-3">
				<div class="relative" style="flex: 1 1 auto; max-width: 480px; min-width: 240px;">
					<label for="behavior-search" class="sr-only">Search behavior categories</label>
					<svg class="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor">
						<path d="M10.68 11.74a6 6 0 0 1-7.922-8.982 6 6 0 0 1 8.982 7.922l3.04 3.04a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215ZM11.5 7a4.499 4.499 0 1 0-8.997 0A4.499 4.499 0 0 0 11.5 7Z" />
					</svg>
					<input
						id="behavior-search"
						type="text"
						placeholder="Search behavior categories…"
						bind:value={behaviorSearch}
						class="form-control w-full"
						style="padding-left: 2rem;"
					/>
				</div>
				<div class="shrink-0">
					<PrimerDropdown
						label="Sort by"
						ariaLabel="Sort behavior categories by behavior status"
						options={BEHAVIOR_SORT_OPTIONS}
						selected={behaviorSort}
						onSelect={(value) => (behaviorSort = value as 'permissible' | 'not_permissible')}
					/>
				</div>
			</div>
			<div class="overflow-hidden rounded-lg border border-border">
				<div class="grid items-center border-b border-border bg-surface px-4 py-2" style="grid-template-columns: {BEHAVIOR_TABLE_COLUMNS}; column-gap: 12px">
					<span class="text-left text-xs font-medium text-text-muted">Behavior category</span>
					<span class="inline-flex items-center gap-1 text-left text-xs font-medium text-text-muted">Behavior status
						<InfoTooltip direction="se" label="Permissible = behavior the target is expected to handle safely. Not permissible = behavior the target must refuse or redirect; engaging counts as a behavior violation." />
					</span>
					<span aria-hidden="true"></span>
				</div>
				{#if visibleBehaviors.length === 0}
					<div class="px-4 py-8 text-center text-sm text-text-muted">No matching behavior categories.</div>
				{:else}
				{#each visibleBehaviors as behavior, idx}
					<div
						role="button"
						tabindex="0"
						class="grid cursor-pointer items-center px-4 py-3 text-left text-sm transition-colors {idx > 0 ? 'border-t border-border' : ''} {selectedBehavior === behavior.name ? 'border-l-2 border-l-interactive bg-interactive/5' : 'hover:bg-surface'}"
						style="grid-template-columns: {BEHAVIOR_TABLE_COLUMNS}; column-gap: 12px"
						onclick={() => selectBehavior(behavior.name)}
						onkeydown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectBehavior(behavior.name); } }}
					>
						<span class="truncate font-medium text-text">{behavior.name}</span>
						<div>
							<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium {behavior.permissible ? 'bg-interactive/15 text-interactive' : 'bg-score-fail/15 text-score-fail'}">
								{behavior.permissible ? 'permissible' : 'not permissible'}
							</span>
						</div>
						<span class="flex justify-end text-text-muted">
							<svg class="h-4 w-4 transition-transform {selectedBehavior === behavior.name ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
						</span>
					</div>
				{/each}
				{/if}
			</div>
		{/if}
	</div>

	{#if selectedBehavior && selectedBehaviorData}
		<div class="sticky top-16 max-h-[calc(100vh-120px)] w-[520px] shrink-0 self-start overflow-y-auto [scrollbar-gutter:stable] rounded-lg border border-border bg-surface">
			<div class="sticky top-0 z-10 border-b border-border bg-surface">
				<div class="px-5 py-3">
				<div class="flex items-start justify-between gap-3">
					<h3 class="min-w-0 break-words text-[16px] font-semibold leading-snug text-text line-clamp-2" title={selectedBehaviorData.name}>{selectedBehaviorData.name}</h3>
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
			</div>

			<div class="px-5 py-4">
				<div class="space-y-5">
						<div>
							<div class="mb-2 flex items-center justify-between gap-2">
								<h4 class="text-xs font-medium text-text">Definition</h4>
								{#if canEdit}
									{#if editingCategory === selectedBehavior}
										<div class="flex shrink-0 items-center gap-2">
											<button class="btn btn-small" onclick={cancelCategoryEdit} disabled={catSaving}>Cancel</button>
											<button class="btn btn-primary btn-small" onclick={saveCategoryEdit} disabled={catSaving}>{catSaving ? 'Saving…' : 'Save'}</button>
										</div>
									{:else}
										<button class="btn btn-small shrink-0" onclick={startCategoryEdit} style="display:inline-flex; align-items:center; gap:0.35rem;">
											<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
											Edit
										</button>
									{/if}
								{/if}
							</div>
							{#if editingCategory === selectedBehavior}
								{#if catSaveError}
									<div class="mb-2 rounded-lg border border-score-fail/40 bg-score-fail/10 px-3 py-2 text-xs text-score-fail">{catSaveError}</div>
								{/if}
								<textarea
									class="form-control w-full text-sm leading-relaxed"
									rows="6"
									aria-label="Behavior category definition"
									bind:value={catDraftDef}
								></textarea>
								<label class="mt-3 flex items-center gap-2 text-sm text-text-secondary">
									<input type="checkbox" bind:checked={catDraftPermissible} />
									Permissible (target may engage without a policy violation)
								</label>
							{:else}
								<div class="prose text-sm leading-relaxed text-text-secondary">{@html renderMarkdown(selectedBehaviorData.definition)}</div>
							{/if}
						</div>
						{#if editingCategory === selectedBehavior}
							<div>
								<div class="mb-2 flex items-center justify-between gap-2">
									<h4 class="text-xs font-medium text-text">Examples</h4>
									<button class="btn btn-small shrink-0" onclick={addDraftExample} disabled={catSaving} style="display:inline-flex; align-items:center; gap:0.35rem;">
										<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/></svg>
										Add example
									</button>
								</div>
								{#if catDraftExamples.length === 0}
									<p class="text-sm text-text-muted">No examples. Use “Add example” to create one.</p>
								{:else}
									<div class="space-y-2">
										{#each catDraftExamples as _, i}
											<div class="flex items-start gap-2">
												<textarea
													class="form-control w-full text-sm leading-relaxed"
													rows="2"
													aria-label="Example {i + 1}"
													bind:value={catDraftExamples[i]}
												></textarea>
												<button
													class="mt-1 shrink-0 rounded p-1 text-text-muted transition-colors hover:bg-surface-2 hover:text-score-fail"
													onclick={() => removeDraftExample(i)}
													disabled={catSaving}
													title="Remove example"
													aria-label="Remove example {i + 1}"
												>
													<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
												</button>
											</div>
										{/each}
									</div>
								{/if}
							</div>
						{:else if selectedBehaviorData.examples?.length > 0}
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
			</div>
		</div>
	{/if}
</div>
{/if}
