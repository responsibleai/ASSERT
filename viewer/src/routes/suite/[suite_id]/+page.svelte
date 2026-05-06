<script lang="ts">
import type { Taxonomy } from '$lib/types.js';
import { goto, invalidateAll } from '$app/navigation';
import { page } from '$app/state';
import { slide } from 'svelte/transition';
import { quintOut } from 'svelte/easing';
import { renderMarkdown } from '$lib/markdown';
import { formatFactorLabel } from '$lib/grouping.js';
import { observedFactorNames } from '$lib/factor-filters.js';
import SeedGroupList from '$lib/SeedGroupList.svelte';
import SystematizationModal from '$lib/SystematizationModal.svelte';
import {
	filterViewerSeeds,
	mergeRunLists,
	groupSeedsByFactor,
	groupSeedsByCrossFactors,
	normalizePromptSeeds,
	normalizeScenarioSeeds
} from '$lib/suite-view.js';

let { data } = $props();

// Tab state — source of truth is URL ?section=
type Tab = 'taxonomy' | 'seeds' | 'results';
const VALID_TABS = new Set<string>(['taxonomy', 'seeds', 'results']);
let activeTab = $derived.by(() => {
	const section = page.url.searchParams.get('section');
	return VALID_TABS.has(section ?? '') ? (section as Tab) : null;
});

function setActiveTab(tab: Tab | null) {
	const url = new URL(page.url);
	if (tab) {
		url.searchParams.set('section', tab);
	} else {
		url.searchParams.delete('section');
	}
	goto(url.toString(), { replaceState: true, noScroll: true, keepFocus: true });
}

// Description expand
let descExpanded = $state(false);

// Metadata panel
let metaOpen = $state(false);

// Systematization expand
let systematizationModalOpen = $state(false);

// Comparison selection
let selectedRuns = $state<Set<string>>(new Set());

function toggleRunSelection(runId: string | null) {
	if (!runId) return;
	const next = new Set(selectedRuns);
	if (next.has(runId)) next.delete(runId);
	else next.add(runId);
	selectedRuns = next;
}

let canCompare = $derived(selectedRuns.size >= 2 && selectedRuns.size <= 4);

// Taxonomy editing
interface EditableBehavior {
	name: string;
	definition: string;
	examples: string[];
	permissible: boolean;
}
type EditablePolicy = Omit<Taxonomy, 'failure_modes'> & { failure_modes: EditableBehavior[] };

let editModalOpen = $state(false);
let editingIndex = $state<number | null>(null);
let editForm = $state<EditableBehavior>({ name: '', definition: '', examples: [], permissible: false });
let editExamplesText = $state('');
let editSaving = $state(false);
let editError = $state<string | null>(null);
let seedsWarningPending = $state(false);
let pendingPolicy = $state<Record<string, unknown> | null>(null);

function openEditModal(idx: number) {
	const sr = sortedBehaviors[idx];
	editingIndex = idx;
	editForm = { name: sr.name, definition: sr.definition, examples: [...sr.examples], permissible: sr.permissible };
	editExamplesText = sr.examples.join('\n');
	editError = null;
	editModalOpen = true;
}

function closeEditModal() {
	editModalOpen = false;
	editingIndex = null;
	editError = null;
}

async function savePolicy(taxonomy: Record<string, unknown>) {
	editSaving = true;
	editError = null;
	try {
		const res = await fetch('/api/taxonomy', {
			method: 'PUT',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ suite_id: data.suite_id, taxonomy })
		});
		const body = await res.json();
		if (!res.ok) { editError = body.error ?? 'Failed to save'; editSaving = false; return false; }
		editSaving = false;
		return true;
	} catch (e: unknown) {
		editError = e instanceof Error ? e.message : 'Failed to save';
		editSaving = false;
		return false;
	}
}

async function handleSaveBehavior() {
	const examples = editExamplesText.split('\n').map(e => e.trim()).filter(Boolean);
	if (!editForm.name.trim()) { editError = 'Name is required'; return; }
	if (!editForm.definition.trim()) { editError = 'Definition is required'; return; }

	const currentTax = data.taxonomy ? structuredClone(data.taxonomy) as EditablePolicy : null;
	if (!currentTax) return;
	const failure_modes = currentTax.failure_modes;
	const entry = { name: editForm.name.trim(), definition: editForm.definition.trim(), examples, permissible: editForm.permissible };

	if (editingIndex === null) return;
	const origName = sortedBehaviors[editingIndex].name;
	const realIdx = failure_modes.findIndex(sr => sr.name === origName);
	if (realIdx >= 0) failure_modes[realIdx] = entry;

	const hasSeeds = data.promptSeeds.length > 0 || data.scenarioSeeds.length > 0;
	if (hasSeeds) {
		pendingPolicy = currentTax;
		seedsWarningPending = true;
		closeEditModal();
		return;
	}

	const ok = await savePolicy(currentTax);
	if (ok) { closeEditModal(); await invalidateAll(); }
}

async function confirmSaveWithSeeds() {
	if (!pendingPolicy) return;
	const ok = await savePolicy(pendingPolicy);
	if (ok) { seedsWarningPending = false; pendingPolicy = null; await invalidateAll(); }
}

// Seeds sub-tab
let seedsSubTab = $state<'prompts' | 'scenarios'>('prompts');

let expandedBehavior = $state<string | null>(null);
let expandedPromptSeedBehavior = $state<string | null>(null);
let promptSeedFilter = $state('');
let promptSeedGroupBy = $state('none');

let expandedAuditBehavior = $state<string | null>(null);
let scenarioSeedFilter = $state('');
let scenarioSeedGroupBy = $state('none');

function toggle(name: string) {
	expandedBehavior = expandedBehavior === name ? null : name;
}

function togglePromptSeedBehavior(name: string) {
	expandedPromptSeedBehavior = expandedPromptSeedBehavior === name ? null : name;
}

function toggleAuditBehavior(name: string) {
	expandedAuditBehavior = expandedAuditBehavior === name ? null : name;
}

let promptSeedItems = $derived(normalizePromptSeeds(data.promptSeeds));
let scenarioSeedItems = $derived(normalizeScenarioSeeds(data.scenarioSeeds));

let promptSeedFactorNames = $derived(observedFactorNames(promptSeedItems));
let scenarioSeedFactorNames = $derived(observedFactorNames(scenarioSeedItems));

let filteredPromptSeeds = $derived(filterViewerSeeds(promptSeedItems, promptSeedFilter));

let sortedBehaviors = $derived(data.taxonomy?.failure_modes ?? []);

let filteredScenarioSeeds = $derived(filterViewerSeeds(scenarioSeedItems, scenarioSeedFilter));

// Truncated description
let specDef = $derived(data.taxonomy?.spec?.definition ?? '');
let needsTruncation = $derived(specDef.length > 120);
let displayDef = $derived(needsTruncation && !descExpanded ? specDef.slice(0, 120) + '…' : specDef);

function summaryItemCountFor(systematization: Record<string, unknown> | null): number {
	if (!systematization) return 0;
	return Array.isArray(systematization.summary_items) ? systematization.summary_items.length : 0;
}

function systematizationModeFor(systematization: Record<string, unknown> | null): string | null {
	if (!systematization) return null;
	const meta = systematization.meta;
	if (!meta || typeof meta !== 'object') return null;
	return typeof (meta as Record<string, unknown>).mode === 'string'
		? String((meta as Record<string, unknown>).mode)
		: null;
}

// Systematization
let hasSystematization = $derived(!!data.systematization);
let summaryItemCount = $derived(
	summaryItemCountFor((data.systematization as Record<string, unknown> | null) ?? null)
);
let systematizationMode = $derived(
	systematizationModeFor((data.systematization as Record<string, unknown> | null) ?? null)
);

const TAB_DESCRIPTIONS: Record<string, string> = {
	taxonomy: 'Spec taxonomy broken into categories with definitions and examples',
	seeds: 'Test seeds and multi-turn scenarios used to evaluate the model',
	results: 'Results from measurement runs',
};

// Merge prompt/audit results that share the same run id.
let allRuns = $derived.by(() => {
	return mergeRunLists(data.runs, data.auditRuns);
});

const TAB_ICONS: Record<string, string> = {
	taxonomy: '<path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>',
	seeds: '<path d="M4 6h16M4 10h16M4 14h16M4 18h16"/>',
	results: '<path d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/>',
};

const tabs: { key: Tab; label: string; count: number }[] = $derived([
	{ key: 'taxonomy', label: 'Taxonomy', count: sortedBehaviors.length },
	{ key: 'seeds', label: 'Seeds', count: data.promptSeeds.length + data.scenarioSeeds.length },
	{ key: 'results', label: 'Results', count: allRuns.length },
]);


</script>

<!-- Header -->
<div class="mb-6">
<a href="/" class="group inline-flex items-center gap-1.5 text-xs text-text-muted transition-colors hover:text-interactive">
<svg class="h-3 w-3 transition-transform group-hover:-translate-x-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M15 19l-7-7 7-7"/></svg>
All measurement suites
</a>
<div class="text-center">
<h1 class="mt-2 text-xl font-semibold tracking-tight">{data.taxonomy?.spec?.name ?? data.suite_id}</h1>
<span class="mt-1.5 inline-block rounded bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-text-muted">{data.suite_id}</span>
{#if data.taxonomy?.spec}
<p class="mx-auto mt-2 max-w-2xl text-sm text-text-secondary leading-relaxed">
{displayDef}
{#if needsTruncation}
<button class="ml-1 text-interactive hover:text-interactive-hover text-xs" onclick={() => descExpanded = !descExpanded}>
{descExpanded ? 'show less' : 'show more'}
</button>
{/if}
</p>
{/if}
<div class="mt-3 flex items-center justify-center gap-2">
<span class="inline-flex items-center gap-1.5 rounded-full bg-surface px-2.5 py-1 text-xs text-text-muted">
<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
{data.suite?.created_at ? new Date(data.suite.created_at).toLocaleDateString() : '—'}
</span>
{#if hasSystematization}
<button
class="inline-flex items-center gap-1 rounded-full bg-surface px-2.5 py-1 text-xs text-text-muted transition-colors hover:text-text-secondary"
onclick={() => metaOpen = !metaOpen}
title="Suite metadata"
>
<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
{metaOpen ? 'hide details' : 'details'}
</button>
{/if}
</div>
{#if metaOpen && (data.suite || hasSystematization)}
<div class="mx-auto mt-3 max-w-2xl rounded-lg border border-border bg-surface p-4 text-left" transition:slide={{ duration: 200, easing: quintOut }}>
{#if hasSystematization}
<div class="mt-3 border-t border-border pt-3">
<h4 class="mb-1.5 text-xs font-semibold uppercase tracking-wider text-text-muted">Systematization Artifacts</h4>
<div class="flex flex-wrap gap-x-4 gap-y-1">
<span class="text-xs text-text-muted"><span class="text-text-secondary">systematization:</span> present</span>
{#if systematizationMode}
<span class="text-xs text-text-muted"><span class="text-text-secondary">mode:</span> {systematizationMode}</span>
{/if}
{#if summaryItemCount > 0}
<span class="text-xs text-text-muted"><span class="text-text-secondary">pattern summaries:</span> {summaryItemCount}</span>
{/if}
<span class="text-xs text-text-muted"><span class="text-text-secondary">taxonomy categories:</span> {sortedBehaviors.length}</span>
</div>
</div>
{/if}
</div>
{/if}
</div>
</div>

<!-- Section cards -->
<div class="mb-6 grid gap-4 sm:grid-cols-3">
{#each tabs as tab}
<button
class="group rounded-lg border p-5 text-left transition-all {activeTab === tab.key ? 'border-interactive bg-surface shadow-sm' : 'border-border bg-surface hover:border-interactive/50 hover:shadow-sm'}"
onclick={() => setActiveTab(activeTab === tab.key ? null : tab.key)}
>
<div class="mb-3 flex items-center justify-between">
<div class="flex items-center gap-2.5">
<div class="flex h-8 w-8 items-center justify-center rounded-lg {activeTab === tab.key ? 'bg-interactive/10' : 'bg-surface-2'}">
<svg class="h-4 w-4 {activeTab === tab.key ? 'text-interactive' : 'text-text-muted'}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">{@html TAB_ICONS[tab.key]}</svg>
</div>
<h2 class="text-sm font-semibold {activeTab === tab.key ? 'text-interactive' : 'text-text'}">{tab.label}</h2>
</div>
<span class="font-mono text-lg font-semibold text-text-secondary">{tab.count}</span>
</div>
<p class="text-xs text-text-muted leading-relaxed">{TAB_DESCRIPTIONS[tab.key]}</p>
</button>
{/each}
</div>

{#if activeTab !== null}
<!-- Tab: Taxonomy -->
{#if activeTab === 'taxonomy'}
{#if hasSystematization}
<!-- Systematization banner -->
<div class="mb-4 rounded-lg border border-border bg-surface p-4">
<div class="flex items-center gap-3 text-xs text-text-muted">
<span class="text-text-secondary font-medium">{sortedBehaviors.length > 0 ? 'Taxonomy generated via' : 'Systematization available'}</span>
<div class="flex items-center gap-1.5">
<button
class="inline-flex items-center gap-1 rounded-full bg-surface-2 px-2.5 py-1 font-medium text-text-secondary hover:text-text border border-transparent hover:border-interactive/30 transition-colors"
onclick={() => systematizationModalOpen = true}
>
<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
Systematization
</button>
{#if sortedBehaviors.length > 0}
<svg class="h-3 w-3 text-text-muted/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
<span class="inline-flex items-center gap-1 rounded-full bg-interactive/10 border border-interactive/20 px-2.5 py-1 font-medium text-interactive">
<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/></svg>
Taxonomy
</span>
{/if}
</div>
</div>
</div>
{/if}
{#if sortedBehaviors.length === 0}
<div class="rounded-lg border border-border bg-surface px-6 py-10 text-center">
<svg class="mx-auto mb-3 h-8 w-8 text-text-muted opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
<path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/>
</svg>
<p class="text-sm text-text-secondary">No taxonomy generated yet.</p>
<p class="mt-1 text-xs text-text-muted">Run the pipeline to generate a spec taxonomy.</p>
</div>
{:else}
<div class="overflow-hidden rounded-lg border border-border">
{#each sortedBehaviors as sr, idx (sr.name)}
<div class="{idx > 0 ? 'border-t border-border' : ''}">
<div class="flex w-full items-center gap-3 px-4 py-2.5 text-sm">
<button
class="flex flex-1 items-center gap-3 text-left transition-colors hover:text-interactive"
onclick={() => toggle(sr.name)}
>
<span class="flex-shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium {sr.permissible ? 'border-interactive/30 bg-interactive/10 text-interactive' : 'border-not-permissible/30 bg-not-permissible/10 text-not-permissible'}">
{sr.permissible ? 'permissible' : 'not permissible'}
</span>
<span class="flex-1 truncate font-medium">{sr.name}</span>
<svg class="h-3.5 w-3.5 text-text-muted transition-transform duration-200 {expandedBehavior === sr.name ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
<path d="M9 5l7 7-7 7"/>
</svg>
</button>
<button onclick={(e) => { e.stopPropagation(); openEditModal(idx); }} class="flex-shrink-0 rounded p-1 text-text-muted hover:text-interactive hover:bg-interactive/10 transition-colors" title="Edit">
<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
</button>
</div>
{#if expandedBehavior === sr.name}
<div transition:slide={{ duration: 200, easing: quintOut }} class="border-t border-border bg-surface px-5 py-5">
<!-- Definition -->
<div class="mb-4">
<h4 class="mb-1.5 text-xs font-semibold uppercase tracking-wider text-text-muted">Definition</h4>
<div class="prose text-sm text-text-secondary leading-relaxed">{@html renderMarkdown(sr.definition)}</div>
</div>

<!-- Examples -->
{#if sr.examples?.length}
<div>
<h4 class="mb-1.5 text-xs font-semibold uppercase tracking-wider text-text-muted">Examples</h4>
<div class="space-y-1.5">
{#each sr.examples as ex}
<div class="border-l-2 border-border pl-3 text-sm text-text-secondary leading-relaxed">{ex}</div>
{/each}
</div>
</div>
{/if}
</div>
{/if}
</div>
{/each}
</div>
{/if}
{/if}

<!-- Tab: Seeds -->
{#if activeTab === 'seeds'}
<!-- Sub-tab toggle -->
<div class="mb-4 flex justify-center">
<div class="flex items-center gap-1 rounded-lg bg-surface p-1">
	<button
		class="rounded-md px-3 py-1.5 text-xs font-medium transition-colors {seedsSubTab === 'prompts' ? 'bg-surface-2 text-text shadow-sm' : 'text-text-muted hover:text-text-secondary'}"
		onclick={() => seedsSubTab = 'prompts'}
	>
		Prompts <span class="ml-1 font-mono text-text-muted">{data.promptSeeds.length}</span>
	</button>
	<button
		class="rounded-md px-3 py-1.5 text-xs font-medium transition-colors {seedsSubTab === 'scenarios' ? 'bg-surface-2 text-text shadow-sm' : 'text-text-muted hover:text-text-secondary'}"
		onclick={() => seedsSubTab = 'scenarios'}
	>
		Scenarios <span class="ml-1 font-mono text-text-muted">{data.scenarioSeeds.length}</span>
	</button>
</div>
</div>

{#if seedsSubTab === 'prompts'}
{#if data.promptSeeds.length === 0}
<div class="rounded-lg border border-border bg-surface px-6 py-10 text-center">
<svg class="mx-auto mb-3 h-8 w-8 text-text-muted opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
<path d="M7 8h10M7 12h4m1 8l-4-4H5a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v8a2 2 0 01-2 2h-3l-4 4z"/>
</svg>
<p class="text-sm text-text-secondary">No prompt seeds generated yet.</p>
<p class="mt-1 text-xs text-text-muted">Run the pipeline to generate prompt seeds.</p>
</div>
{:else}
<div class="mb-3 flex flex-wrap items-center gap-3">
<div class="relative">
<svg class="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
</svg>
<input
type="text"
placeholder="Search seeds…"
bind:value={promptSeedFilter}
class="rounded-md border border-border bg-surface py-1.5 pl-8 pr-3 text-sm text-text placeholder-text-muted outline-none transition-colors focus:border-interactive focus:ring-1 focus:ring-interactive/50"
/>
</div>
<span class="text-xs text-text-muted">{filteredPromptSeeds.length} of {data.promptSeeds.length}</span>
<div class="ml-auto flex items-center gap-2">
	<span class="text-[10px] text-text-muted">Group by</span>
	<select
		class="rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-interactive"
		bind:value={promptSeedGroupBy}
	>
		<option value="none">None (flat)</option>
		{#each promptSeedFactorNames as name}
			<option value="factor:{name}">{formatFactorLabel(name)}</option>
		{/each}
		{#each promptSeedFactorNames as name, i}
			{#each promptSeedFactorNames.slice(i + 1) as other}
				<option value="cross:{name}:{other}">{formatFactorLabel(name)} × {formatFactorLabel(other)}</option>
			{/each}
		{/each}
	</select>
</div>
</div>
{#if promptSeedGroupBy === 'none'}
<SeedGroupList
	groups={[{ name: '', items: filteredPromptSeeds }]}
	expandedGroup={''}
	onToggle={() => {}}
/>
{:else if promptSeedGroupBy.startsWith('cross:')}
{@const parts = promptSeedGroupBy.split(':')}
<SeedGroupList
	groups={groupSeedsByCrossFactors(filteredPromptSeeds, parts[1], parts[2])}
	expandedGroup={expandedPromptSeedBehavior}
	onToggle={togglePromptSeedBehavior}
/>
{:else}
{@const factorName = promptSeedGroupBy.replace('factor:', '')}
<SeedGroupList
	groups={groupSeedsByFactor(filteredPromptSeeds, factorName)}
	expandedGroup={expandedPromptSeedBehavior}
	onToggle={togglePromptSeedBehavior}
/>
{/if}
{/if}

{:else}
<!-- Scenarios sub-tab -->
{#if data.scenarioSeeds.length === 0}
<div class="rounded-lg border border-border bg-surface px-6 py-10 text-center">
<svg class="mx-auto mb-3 h-8 w-8 text-text-muted opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
<path d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
</svg>
<p class="text-sm text-text-secondary">No audit scenarios generated yet.</p>
<p class="mt-1 text-xs text-text-muted">Generate adversarial multi-turn scenario seeds.</p>
</div>
{:else}
<div class="mb-3 flex flex-wrap items-center gap-3">
<div class="relative">
<svg class="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
<circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
</svg>
<input
type="text"
placeholder="Search seeds…"
bind:value={scenarioSeedFilter}
class="rounded-md border border-border bg-surface py-1.5 pl-8 pr-3 text-sm text-text placeholder-text-muted outline-none transition-colors focus:border-interactive focus:ring-1 focus:ring-interactive/50"
/>
</div>
<span class="text-xs text-text-muted">{filteredScenarioSeeds.length} of {data.scenarioSeeds.length}</span>
<div class="ml-auto flex items-center gap-2">
	<span class="text-[10px] text-text-muted">Group by</span>
	<select
		class="rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-interactive"
		bind:value={scenarioSeedGroupBy}
	>
		<option value="none">None (flat)</option>
		{#each scenarioSeedFactorNames as name}
			<option value="factor:{name}">{formatFactorLabel(name)}</option>
		{/each}
		{#each scenarioSeedFactorNames as name, i}
			{#each scenarioSeedFactorNames.slice(i + 1) as other}
				<option value="cross:{name}:{other}">{formatFactorLabel(name)} × {formatFactorLabel(other)}</option>
			{/each}
		{/each}
	</select>
</div>
</div>
{#if scenarioSeedGroupBy === 'none'}
<SeedGroupList
	groups={[{ name: '', items: filteredScenarioSeeds }]}
	expandedGroup={''}
	onToggle={() => {}}
/>
{:else if scenarioSeedGroupBy.startsWith('cross:')}
{@const parts = scenarioSeedGroupBy.split(':')}
<SeedGroupList
	groups={groupSeedsByCrossFactors(filteredScenarioSeeds, parts[1], parts[2])}
	expandedGroup={expandedAuditBehavior}
	onToggle={toggleAuditBehavior}
/>
{:else}
{@const factorName = scenarioSeedGroupBy.replace('factor:', '')}
<SeedGroupList
	groups={groupSeedsByFactor(filteredScenarioSeeds, factorName)}
	expandedGroup={expandedAuditBehavior}
	onToggle={toggleAuditBehavior}
/>
{/if}
{/if}
{/if}
{/if}

<!-- Tab: Results -->
{#if activeTab === 'results'}

{#if allRuns.length === 0}
<div class="rounded-lg border border-border bg-surface px-6 py-10 text-center">
<svg class="mx-auto mb-3 h-8 w-8 text-text-muted opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
<path d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z"/><path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
</svg>
<p class="text-sm text-text-secondary">No evaluation runs found for this suite.</p>
<p class="mt-1 text-xs text-text-muted">Add results under <code>artifacts/results/{data.suite_id}</code> to browse them here.</p>
</div>
{:else if allRuns.length > 0}
<!-- Compare button (sticky) -->
{#if selectedRuns.size >= 1}
<div class="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-2.5 mb-1">
	<span class="text-xs text-text-muted">{selectedRuns.size} selected</span>
	{#if canCompare}
		<a href="/suite/{data.suite_id}/compare?runs={[...selectedRuns].join(',')}"
			class="inline-flex items-center gap-1.5 rounded-md bg-interactive px-3 py-1 text-xs font-medium text-white hover:bg-interactive-hover transition-colors">
			Compare runs
			<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M13 7l5 5m0 0l-5 5m5-5H6"/></svg>
		</a>
	{:else}
		<span class="text-[10px] text-text-muted">Select {2 - selectedRuns.size} more to compare</span>
	{/if}
	<button onclick={() => { selectedRuns = new Set(); }} class="ml-auto text-[10px] text-text-muted hover:text-interactive transition-colors">Clear</button>
</div>
{/if}

<div class="space-y-3">
{#each allRuns as run (run.run_id)}
{@const qRun = run.prompt}
{@const aRun = run.audit}
{@const qAvg = qRun?.metrics ? qRun.metrics.policy_violation_rate : 0}
{@const aAvg = aRun?.metrics ? aRun.metrics.policy_violation_rate : 0}
{@const bestAvg = Math.max(qAvg, aAvg)}
{@const isSelected = run.compare_run_id ? selectedRuns.has(run.compare_run_id) : false}
{@const compareDisabled = !run.compare_run_id}
<div class="rounded-lg border bg-surface overflow-hidden border-l-[3px] transition-colors duration-150 {isSelected ? 'border-interactive/50 border-l-interactive' : bestAvg >= 0.5 ? 'border-border border-l-score-fail' : bestAvg > 0 ? 'border-border border-l-score-border' : 'border-border border-l-score-pass'}">
<!-- Run header -->
<div class="flex items-center gap-3 px-4 py-2.5 border-b border-border/50">
<button onclick={() => toggleRunSelection(run.compare_run_id)}
	class="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition-colors duration-150 {compareDisabled ? 'cursor-not-allowed border-text-muted/20 opacity-40' : isSelected ? 'border-interactive bg-interactive' : 'border-text-muted/40 hover:border-interactive/60'}"
	title={compareDisabled ? 'Prompt results required for compare' : 'Select for comparison'}
	disabled={compareDisabled}>
	{#if isSelected}
		<svg class="h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"/></svg>
	{/if}
</button>
<div class="min-w-0">
<div class="truncate text-sm font-medium text-interactive">{run.run_id}</div>
<div class="truncate font-mono text-[10px] text-text-muted">{run.run_id}</div>
</div>
</div>

<!-- Query row -->
{#if qRun}
<a href="/suite/{data.suite_id}/{run.prompt_run_id ?? run.run_id}?tab=prompts"
class="flex flex-wrap items-center gap-2 px-4 py-2.5 transition-colors hover:bg-surface/50 {aRun ? 'border-b border-border/30' : ''}">
<span class="w-20 flex-shrink-0 text-[10px] font-semibold uppercase tracking-wider text-text-muted">Prompts</span>
{#if qRun.metrics}
{@const total = qRun.metrics.total}
{@const avg = qRun.metrics.policy_violation_rate}
{@const judgeFailures = qRun.metrics.judge_failures}
<span class="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px]">
<span class="text-text-muted">taxonomy violation</span>
<span class="font-semibold tabular-nums {avg >= 0.5 ? 'text-score-fail' : avg > 0 ? 'text-score-border' : 'text-score-pass'}">{(avg * 100).toFixed(0)}%</span>
</span>
{#each Object.entries(qRun.metrics.dimensions ?? {}).filter(([k]) => k !== 'policy_violation' && k !== 'overrefusal') as [dim, d]}
<span class="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px]">
<span class="text-text-muted">{dim.replace(/_/g, ' ')}</span>
<span class="font-semibold tabular-nums {d.rate >= 0.5 ? 'text-score-fail' : d.rate > 0 ? 'text-score-border' : 'text-score-pass'}">{(d.rate * 100).toFixed(0)}%</span>
</span>
{/each}
<span class="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px] text-text-muted">
target: <span class="font-mono">{qRun.metrics.target}</span>
</span>
{#if qRun.metrics.judge_model}
<span class="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px] text-text-muted">
judge: <span class="font-mono">{qRun.metrics.judge_model}</span>
</span>
{/if}
{#if judgeFailures > 0}
<span class="inline-flex items-center gap-1 rounded border border-amber-500/25 bg-amber-500/8 px-2 py-0.5 text-[10px] text-amber-300">
{judgeFailures} judgment{judgeFailures === 1 ? '' : 's'} failed
</span>
{/if}
<span class="ml-auto text-[10px] text-text-muted">{total} prompts</span>
{/if}
</a>
{/if}

<!-- Scenarios row -->
{#if aRun}
<a href="/suite/{data.suite_id}/{run.audit_run_id ?? run.run_id}?tab=audit"
class="flex flex-wrap items-center gap-2 px-4 py-2.5 transition-colors hover:bg-surface/50">
<span class="w-20 flex-shrink-0 text-[10px] font-semibold uppercase tracking-wider text-text-muted">Scenarios</span>
{#if aRun.metrics}
{@const avg = aRun.metrics.policy_violation_rate}
{@const judgeFailures = aRun.metrics.judge_failures}
<span class="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px]">
<span class="text-text-muted">taxonomy violation</span>
<span class="font-semibold tabular-nums {avg >= 0.5 ? 'text-score-fail' : avg > 0 ? 'text-score-border' : 'text-score-pass'}">{(avg * 100).toFixed(0)}%</span>
</span>
{#each Object.entries(aRun.metrics.dimensions ?? {}).filter(([k]) => k !== 'policy_violation' && k !== 'overrefusal') as [dim, d]}
<span class="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px]">
<span class="text-text-muted">{dim.replace(/_/g, ' ')}</span>
<span class="font-semibold tabular-nums {d.rate >= 0.5 ? 'text-score-fail' : d.rate > 0 ? 'text-score-border' : 'text-score-pass'}">{(d.rate * 100).toFixed(0)}%</span>
</span>
{/each}
<span class="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px] text-text-muted">
target: <span class="font-mono">{aRun.metrics.target}</span>
</span>
{#if aRun.metrics.tester_model}
<span class="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px] text-text-muted">
tester: <span class="font-mono">{aRun.metrics.tester_model}</span>
</span>
{/if}
{#if aRun.metrics.judge_model}
<span class="inline-flex items-center gap-1 rounded border border-border bg-background px-2 py-0.5 text-[10px] text-text-muted">
judge: <span class="font-mono">{aRun.metrics.judge_model}</span>
</span>
{/if}
{#if judgeFailures > 0}
<span class="inline-flex items-center gap-1 rounded border border-amber-500/25 bg-amber-500/8 px-2 py-0.5 text-[10px] text-amber-300">
{judgeFailures} judgment{judgeFailures === 1 ? '' : 's'} failed
</span>
{/if}
<span class="ml-auto text-[10px] text-text-muted">{aRun.metrics.total} scenarios</span>
{/if}
</a>
{/if}
</div>
{/each}
</div>
{/if}
{/if}
{/if}

<!-- FailureMode Editor Modal -->
{#if editModalOpen}
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="fixed inset-0 z-50 flex items-center justify-center p-4" onkeydown={(e) => { if (e.key === 'Escape') closeEditModal(); }}>
<button class="absolute inset-0 bg-black/60 backdrop-blur-sm" onclick={() => closeEditModal()} aria-label="Close"></button>
<div class="relative w-full max-w-lg rounded-xl border border-border bg-bg shadow-2xl flex flex-col">
<div class="flex items-center justify-between border-b border-border px-6 py-4">
<h2 class="text-base font-semibold text-text">Edit Category</h2>
<button onclick={() => closeEditModal()} aria-label="Close category editor" class="rounded-lg p-1.5 text-text-muted hover:text-text hover:bg-surface transition-colors">
<svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6 18L18 6M6 6l12 12"/></svg>
</button>
</div>
<div class="px-6 py-5 space-y-4">
<div>
<label for="sr-name" class="block text-xs font-medium text-text-secondary mb-1">Name</label>
<input id="sr-name" type="text" bind:value={editForm.name} disabled
class="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text outline-none focus:border-interactive opacity-60" />
</div>
<div>
<label for="sr-def" class="block text-xs font-medium text-text-secondary mb-1">Definition</label>
<textarea id="sr-def" bind:value={editForm.definition} rows={4}
class="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text leading-relaxed outline-none focus:border-interactive resize-y"></textarea>
</div>
<div>
<label for="sr-examples" class="block text-xs font-medium text-text-secondary mb-1">Examples <span class="text-text-muted font-normal">(one per line)</span></label>
<textarea id="sr-examples" bind:value={editExamplesText} rows={4}
class="w-full rounded-md border border-border bg-bg px-3 py-2 text-sm text-text leading-relaxed outline-none focus:border-interactive resize-y font-mono"></textarea>
</div>
<div class="flex items-center gap-3">
<span class="text-xs font-medium text-text-secondary">FailureMode</span>
<button
onclick={() => editForm.permissible = !editForm.permissible}
class="inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors {editForm.permissible ? 'bg-interactive/10 text-interactive border border-interactive/30' : 'bg-not-permissible/10 text-not-permissible border border-not-permissible/30'}"
>
{editForm.permissible ? 'permissible' : 'not permissible'}
</button>
</div>
{#if editError}
<p class="text-xs text-not-permissible">{editError}</p>
{/if}
</div>
<div class="flex items-center justify-end gap-3 border-t border-border px-6 py-4">
<button onclick={() => closeEditModal()} class="rounded-md px-4 py-2 text-sm text-text-muted hover:text-text transition-colors">Cancel</button>
<button onclick={() => handleSaveBehavior()} disabled={editSaving}
class="rounded-md bg-interactive px-4 py-2 text-sm font-medium text-white hover:bg-interactive-hover transition-colors disabled:opacity-50">
{editSaving ? 'Saving…' : 'Save changes'}
</button>
</div>
</div>
</div>
{/if}

<!-- Seeds Warning Modal -->
{#if seedsWarningPending}
<div class="fixed inset-0 z-50 flex items-center justify-center p-4">
<button class="absolute inset-0 bg-black/60 backdrop-blur-sm" onclick={() => { seedsWarningPending = false; pendingPolicy = null; }} aria-label="Close"></button>
<div class="relative w-full max-w-md rounded-xl border border-border bg-bg shadow-2xl p-6">
<div class="flex items-start gap-3">
<div class="flex-shrink-0 mt-0.5 flex h-8 w-8 items-center justify-center rounded-full bg-yellow-500/10">
<svg class="h-4 w-4 text-yellow-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4.5c-.77-.833-2.694-.833-3.464 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z"/></svg>
</div>
<div>
<h3 class="text-sm font-semibold text-text">Existing seeds won't be updated</h3>
<p class="mt-1.5 text-xs text-text-muted leading-relaxed">
This suite has <strong class="text-text-secondary">{data.promptSeeds.length} prompts</strong>
{#if data.scenarioSeeds.length > 0}
and <strong class="text-text-secondary">{data.scenarioSeeds.length} scenarios</strong>
{/if}
that were generated from the previous taxonomy. Editing the taxonomy won't update them — you'll need to regenerate seeds for changes to take effect.
</p>
</div>
</div>
<div class="mt-5 flex justify-end gap-3">
<button onclick={() => { seedsWarningPending = false; pendingPolicy = null; }} class="rounded-md px-4 py-2 text-sm text-text-muted hover:text-text transition-colors">Cancel</button>
<button onclick={() => confirmSaveWithSeeds()} disabled={editSaving}
class="rounded-md bg-interactive px-4 py-2 text-sm font-medium text-white hover:bg-interactive-hover transition-colors disabled:opacity-50">
{editSaving ? 'Saving…' : 'Save anyway'}
</button>
</div>
</div>
</div>
{/if}



<!-- Systematization Modal -->
<SystematizationModal
	bind:open={systematizationModalOpen}
	systematization={data.systematization as Record<string, unknown> | null}
/>
