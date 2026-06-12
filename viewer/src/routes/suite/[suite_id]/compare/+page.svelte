<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import { getJudgeError, getRecordFlag, getRequiredBaseMetricNames, inferJudgeStatus } from '$lib/judgment.js';
	import { buildMatchedSampleRows } from '$lib/compare-view.js';
	import PrimerDropdown from '$lib/PrimerDropdown.svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import type { BinaryCounts, DimensionDef, InteractionMessage, JudgedSample } from '$lib/types.js';

	let { data } = $props();
	let requiredBaseMetrics = $derived(
		getRequiredBaseMetricNames(data.dimensionDefs as Record<string, DimensionDef>)
	);

	function judgeStatus(record: { verdict?: Record<string, unknown> | null; judge_status?: string | null }) {
		return inferJudgeStatus(record, requiredBaseMetrics);
	}

const RUN_COLORS = ['#a78bfa', '#60a5fa', '#2dd4bf', '#fbbf24'];

// Baseline is always the first run
let baselineIdx = $state(0);

// Result type toggle: prompts (single-turn, default) vs scenarios (multi-turn).
// URL contract is `?kind=prompts|scenarios` — note the new compare-page param
// name intentionally diverges from the legacy run-page `?tab=prompts|audit`
// so the public URL surface matches the user-visible "Scenarios" label here
// without breaking existing run-page bookmarks.
let activeKind = $derived(
	((data as { kind?: string }).kind === 'scenarios' ? 'scenarios' : 'prompts') as
		| 'prompts'
		| 'scenarios'
);
let emptyKind = $derived(Boolean((data as { emptyKind?: boolean }).emptyKind));

function setActiveKind(kind: 'prompts' | 'scenarios') {
	if (kind === activeKind) return;
	const url = new URL(page.url);
	if (kind === 'scenarios') url.searchParams.set('kind', 'scenarios');
	else url.searchParams.delete('kind');
	goto(url.toString(), { replaceState: true, noScroll: true, keepFocus: true });
}

// Expanded behavior rows
let expandedRows = $state<Set<string>>(new Set());

// "Disagreements only" filter
let disagreementsOnly = $state(false);

// Active metric for comparison
let activeMetric = $state('policy_violation');

function metricLabel(m: string): string {
	return m.replace(/_/g, ' ');
}

// Short label for a run's target. Callable targets ("module.path:function_name")
// reduce to "function_name"; provider/model strings ("provider/model-name")
// reduce to "model-name". Avoids overflowing column headers and per-run cards.
function runLabel(model: string | null | undefined): string {
	const m = model ?? '';
	if (m.includes(':')) return m.split(':').pop() || m;
	if (m.includes('/')) return m.split('/').pop() || m;
	return m;
}

// Re-sort comparisons by active metric's delta
let sortedComparisons = $derived.by(() => {
	return [...data.comparisons].sort((a, b) =>
		Math.abs(b.deltas[activeMetric] ?? 0) - Math.abs(a.deltas[activeMetric] ?? 0)
	);
});

function toggleRow(behavior: string) {
	const next = new Set(expandedRows);
	if (next.has(behavior)) next.delete(behavior);
	else next.add(behavior);
	expandedRows = next;
}

// Show-all tracker per behavior
let showAllMap = $state<Record<string, boolean>>({});

function rateColor(rate: number): string {
	if (rate >= 0.5) return 'var(--theme-score-fail)';
	if (rate > 0) return 'var(--theme-score-border)';
	return 'var(--theme-score-pass)';
}

function rateTextClass(rate: number): string {
	if (rate >= 0.5) return 'text-score-fail';
	if (rate > 0) return 'text-score-border';
	return 'text-score-pass';
}

function deltaText(d: number): string {
	if (d > 0) return `+${(d * 100).toFixed(0)}%`;
	if (d < 0) return `${(d * 100).toFixed(0)}%`;
	return '0%';
}

function deltaClass(d: number): string {
	if (d > 0.05) return 'text-score-fail';
	if (d < -0.05) return 'text-score-pass';
	return 'text-text-muted';
}

function deltaArrow(d: number): string {
	if (d > 0.05) return '▲';
	if (d < -0.05) return '▼';
	return '';
}

let runIds = $derived(data.runs.map((run) => run.run_id));

// Issue 1: baseline-first display order + run-id-keyed colors so a run keeps its color across pages
let orderedRuns = $derived([
	data.runs[baselineIdx],
	...data.runs.filter((_, i) => i !== baselineIdx)
]);
let runColor = $derived(
	Object.fromEntries(data.runs.map((r, i) => [r.run_id, RUN_COLORS[i]])) as Record<string, string>
);
function baselineDeltaFor(run: { run_id: string; policyViolationRate: number; dimensions: Record<string, { rate: number }> }) {
	const baseline = data.runs[baselineIdx];
	const avg = activeMetric === 'policy_violation' ? run.policyViolationRate : (run.dimensions[activeMetric]?.rate ?? 0);
	const baselineAvg = activeMetric === 'policy_violation' ? baseline.policyViolationRate : (baseline.dimensions[activeMetric]?.rate ?? 0);
	return { avg, baselineAvg, delta: run.run_id === baseline.run_id ? 0 : avg - baselineAvg };
}

function getMatchedSamples(behavior: string) {
	return buildMatchedSampleRows(
		data.samplesByBehavior[behavior] ?? {},
		runIds,
		activeMetric,
		disagreementsOnly,
		data.runs[baselineIdx]?.run_id,
		// In scenarios mode, pair multi-turn conversations by the shared
		// test_case_id. The opening prompt (first user turn) is identical
		// across runs because both replays seed from the same test_set.jsonl,
		// but assistant responses diverge after turn 1 as the adaptive tester
		// drives each variant differently. Fall back to prompt text when a
		// row is missing test_case_id (legacy artifacts) so pairing is still
		// best-effort instead of dropping the row entirely.
		activeKind === 'scenarios' ? (s: JudgedSample) => s.test_case_id ?? s.prompt : undefined
	);
}

// Conversation messages minus system prompts. Used by scenarios mode to
// render each run's full multi-turn transcript below the shared seed turn.
function conversationMessages(sample: JudgedSample | null): InteractionMessage[] {
	if (!sample?.messages) return [];
	return sample.messages.filter((m) => m.role !== 'system');
}

// Opening user turn for a scenario row. Per `buildJudgedSampleRow` in
// server/data.ts, JudgedSample.prompt already holds the first user message,
// which is the same across runs in scenarios mode (the seed).
function openingPrompt(samples: Record<string, JudgedSample | null>): string {
	for (const sample of Object.values(samples)) {
		if (sample?.prompt) return sample.prompt;
	}
	return '';
}

function turnRoleLabel(role: InteractionMessage['role']): string {
	if (role === 'user') return 'User';
	if (role === 'assistant') return 'Assistant';
	if (role === 'tool') return 'Tool';
	return role;
}

function pctBar(counts: BinaryCounts): { clear: number; flagged: number } {
	const total = counts[0] + counts[1];
	if (total === 0) return { clear: 0, flagged: 0 };
	return {
		clear: (counts[0] / total) * 100,
		flagged: (counts[1] / total) * 100
	};
}

function scoreBadgeClass(flag: boolean): string {
	return flag ? 'bg-score-fail/20 text-score-fail' : 'bg-score-pass/20 text-score-pass';
}

function summaryGridTemplate(): string {
	return 'repeat(auto-fit, minmax(220px, 1fr))';
}

function comparisonGridTemplate(runCount: number): string {
	return `minmax(14rem, 1fr) repeat(${runCount}, 120px)`;
}

function comparisonTableMinWidth(runCount: number): string {
	return `${14 + runCount * 7.5}rem`;
}

function sampleGridTemplate(runCount: number): string {
	return `repeat(${runCount}, minmax(16rem, 1fr))`;
}

function sampleGridMinWidth(runCount: number): string {
	return `${runCount * 16}rem`;
}

function capitalize(s: string): string {
	return s.charAt(0).toUpperCase() + s.slice(1);
}
</script>

<div class="mb-6">
	<nav aria-label="Breadcrumb">
		<ol class="Breadcrumb">
			<li class="Breadcrumb-item"><a href="/">Evaluation suites</a></li>
			<li class="Breadcrumb-item"><a href="/suite/{data.suite_id}">{data.taxonomy?.behavior?.name ?? data.suite_id}</a></li>
			<li class="Breadcrumb-item" aria-current="page">Compare runs</li>
		</ol>
	</nav>

	<!-- ═══ SECTION 1: Header ═══ -->
	<div class="mt-5">
		<div class="text-[12px] font-medium text-text-muted">Comparison</div>
		<h1 class="text-2xl font-semibold leading-tight text-text" style="margin-top:2px;">
			Comparing {data.runs.length} runs on {data.taxonomy?.behavior?.name ?? data.suite_id}
		</h1>

		<div class="mt-4 flex flex-wrap items-end gap-4">
			<!-- Baseline dropdown -->
			<div>
				<label for="baseline-select" class="block text-xs font-medium text-text-muted mb-1.5">Baseline</label>
				<PrimerDropdown
					ariaLabel="Select baseline run"
					selected={String(baselineIdx)}
					options={data.runs.map((run, i) => ({
						value: String(i),
						label: `${run.display_name} · ${run.date}`
					}))}
					onSelect={(value) => { baselineIdx = Number(value); }}
				/>
			</div>

			<!-- Result type toggle (same design language as the single-run view) -->
			<div>
				<span class="block text-xs font-medium text-text-muted mb-1.5">Result type</span>
				<div class="SegmentedControl" role="tablist" aria-label="Result type">
					<button
						type="button"
						role="tab"
						aria-selected={activeKind === 'prompts'}
						class="SegmentedControl-item"
						class:SegmentedControl-item--selected={activeKind === 'prompts'}
						onclick={() => setActiveKind('prompts')}
					>
						<span class="SegmentedControl-content">
							<span>Prompts</span>
						</span>
					</button>
					<button
						type="button"
						role="tab"
						aria-selected={activeKind === 'scenarios'}
						class="SegmentedControl-item"
						class:SegmentedControl-item--selected={activeKind === 'scenarios'}
						onclick={() => setActiveKind('scenarios')}
					>
						<span class="SegmentedControl-content">
							<span>Scenarios</span>
						</span>
					</button>
				</div>
			</div>
		</div>
	</div>
</div>

{#if emptyKind}
	<div class="rounded-lg border border-border bg-surface px-6 py-12 text-center">
		<svg class="mx-auto mb-4 h-10 w-10 text-text-muted opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
			<path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/>
		</svg>
		<p class="text-sm text-text-secondary">No scenario evaluations recorded for these runs.</p>
		<p class="mt-2 text-xs text-text-muted">Switch back to Prompts to view this comparison.</p>
	</div>
{:else}
<div class="space-y-8">
	<!-- ═══ SECTION 2: Metric Picker + Summary Cards ═══ -->
	<section class="space-y-4">
		<div class="flex items-center justify-between gap-3">
			<h2 class="min-w-0 flex-1 truncate text-lg font-semibold text-text">Summary</h2>
			{#if data.allMetrics.length > 1}
				<div class="flex shrink-0 items-center gap-2">
					<span class="text-xs text-text-muted">Metric</span>
					<PrimerDropdown
						label=""
						ariaLabel="Metric"
						options={data.allMetrics.map((metric) => ({ value: metric, label: capitalize(metricLabel(metric)) }))}
						selected={activeMetric}
						onSelect={(value) => { activeMetric = value; }}
					/>
				</div>
			{:else}
				<span class="shrink-0 text-xs text-text-muted">{capitalize(metricLabel(activeMetric))}</span>
			{/if}
		</div>

		<div class="grid gap-4" style="grid-template-columns: {summaryGridTemplate()};">
			{#each orderedRuns as run (run.run_id)}
				{@const isBaseline = run.run_id === data.runs[baselineIdx].run_id}
				{@const dInfo = baselineDeltaFor(run)}
				{@const avg = dInfo.avg}
				{@const delta = dInfo.delta}
				{@const runScores = activeMetric === 'policy_violation' ? run.counts : (run.dimensions[activeMetric]?.counts ?? { 0: 0, 1: 0 })}
				{@const pct = pctBar(runScores)}
				{@const totalSamples = runScores[0] + runScores[1]}
				<div class="rounded-lg border border-border bg-surface px-5 py-4">
					<!-- Header: run name + sample count -->
					<div class="flex items-start justify-between gap-3">
						<div class="flex items-center gap-2 min-w-0">
							<span class="h-2 w-2 rounded-full flex-shrink-0" style="background: {runColor[run.run_id]}"></span>
							<span class="text-sm font-medium text-text truncate">{run.display_name}</span>
							{#if isBaseline}
								<span class="inline-flex items-center rounded-full bg-interactive/15 px-2 py-0.5 text-[10px] font-semibold text-interactive ring-1 ring-interactive/40">Baseline</span>
							{/if}
						</div>
						<span class="shrink-0 text-[12px] text-text-muted tabular-nums">{run.total} samples</span>
					</div>

					<!-- Model name -->
					<div class="mt-1 font-mono text-xs text-text-muted truncate" title={run.model}>{runLabel(run.model)}</div>

					<!-- Big number -->
					<div class="mt-3 flex items-baseline gap-1.5">
						<span class="text-3xl font-bold tabular-nums text-text">{(avg * 100).toFixed(0)}%</span>
						<span class="text-sm text-text-muted">Flagged</span>
						{#if !isBaseline && Math.abs(delta) >= 0.005}
							<span class="ml-1 text-sm font-semibold tabular-nums {deltaClass(delta)}">
								{deltaText(delta)} {deltaArrow(delta)}
							</span>
						{/if}
					</div>

					<!-- Score distribution bar (matches run detail summary card) -->
					{#if totalSamples > 0}
						<div class="mt-2.5 flex h-1.5 overflow-hidden rounded-full bg-border/50">
							{#if pct.flagged > 0}
								<div class="bg-score-fail" style="width: {pct.flagged}%"></div>
							{/if}
							{#if pct.clear > 0}
								<div class="bg-score-pass" style="width: {pct.clear}%"></div>
							{/if}
						</div>
						<div class="mt-1 flex justify-between text-[12px] tabular-nums text-text-muted">
							<span>{runScores[1]}/{totalSamples} Flagged</span>
							<span>{runScores[0]}/{totalSamples} Pass</span>
						</div>
					{/if}

					<div class="mt-3 text-xs text-text-muted">
						{#if run.judgeFailures > 0}
							{run.scoredTotal} scored / {run.total} total · {run.judgeFailures} judge failures · Judge: <span class="font-mono">{run.judge_model}</span>
						{:else}
							Judge: <span class="font-mono">{run.judge_model}</span>
						{/if}
					</div>

					<!-- Multi-judge agreement (only when data exists) -->
					{#if run.meanAgreement !== null}
						<div class="space-y-1 pt-2 border-t border-border">
							<div class="flex justify-between text-xs">
								<span class="text-text-muted">Judge agreement</span>
								<span class="font-semibold tabular-nums {run.meanAgreement >= 0.8 ? 'text-score-pass' : run.meanAgreement >= 0.6 ? 'text-score-border' : 'text-score-fail'}">{(run.meanAgreement * 100).toFixed(0)}%</span>
							</div>
							{#if run.highVarianceCount > 0}
								<div class="text-xs text-score-border">{run.highVarianceCount} sample{run.highVarianceCount > 1 ? 's' : ''} with high variance</div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	</section>

	<!-- ═══ SECTION 3: Behavior Heatmap ═══ -->
	<section class="space-y-3">
		<div class="flex items-center justify-between gap-3 flex-wrap">
			<h2 class="text-lg font-semibold text-text">By behavior category</h2>

			<!-- Disagreements toggle -->
			<span class="ToggleSwitch ToggleSwitch--small">
				<span class="ToggleSwitch-statusLabel ToggleSwitch-statusLabel--muted">Disagreements only</span>
				<button
					type="button"
					role="switch"
					aria-checked={disagreementsOnly}
					aria-label="Show disagreements only"
					class="ToggleSwitch-track"
					onclick={() => { disagreementsOnly = !disagreementsOnly; }}
				>
					<span class="ToggleSwitch-icons" aria-hidden="true">
						<span class="ToggleSwitch-lineIcon">
							<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><path d="M3.75 7.25h8.5a.75.75 0 0 1 0 1.5h-8.5a.75.75 0 0 1 0-1.5Z"/></svg>
						</span>
						<span class="ToggleSwitch-circleIcon">
							<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="3.25"/></svg>
						</span>
					</span>
					<span class="ToggleSwitch-knob"></span>
				</button>
			</span>
		</div>

		<!-- Table -->
		<div class="overflow-x-auto rounded-lg border border-border">
			<div class="min-w-max" style="min-width: {comparisonTableMinWidth(data.runs.length)};">
				<div class="grid items-center gap-2 px-4 py-2.5 bg-surface text-xs font-semibold text-text-muted border-b border-border"
					style="grid-template-columns: {comparisonGridTemplate(data.runs.length)};">
					<span>Behavior</span>
					{#each orderedRuns as run (run.run_id)}
						<span class="text-center font-mono font-medium truncate" title={run.model} style="color: {runColor[run.run_id]}">{runLabel(run.model)}</span>
					{/each}
				</div>

				<!-- Rows -->
				{#each sortedComparisons as row (row.behavior)}
					{@const isExpanded = expandedRows.has(row.behavior)}
					{@const matched = isExpanded ? getMatchedSamples(row.behavior) : []}
					{@const showAll = showAllMap[row.behavior] ?? false}
					{@const displaySamples = showAll ? matched : matched.slice(0, 3)}
					{@const rowDelta = row.deltas[activeMetric] ?? 0}

					<div class="border-b border-border last:border-b-0">
						<!-- Row -->
						<button
							onclick={() => toggleRow(row.behavior)}
							class="w-full grid items-center gap-2 px-4 py-3 text-left transition-colors duration-150 hover:bg-surface-2 cursor-pointer"
							style="grid-template-columns: {comparisonGridTemplate(data.runs.length)};"
						>
						<!-- Behavior name -->
						<div class="flex items-center gap-2 min-w-0">
							<svg class="h-3.5 w-3.5 flex-shrink-0 text-text-muted transition-transform duration-150 {isExpanded ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
								<path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/>
							</svg>
							<span class="text-sm text-text truncate">{row.behavior}</span>
						</div>

						<!-- Score cells -->
						{#each orderedRuns as run (run.run_id)}
							{@const cell = row.metrics[activeMetric]?.[run.run_id]}
							{@const hasFlagged = cell && cell.counts[1] > 0}
							{@const cellColor = hasFlagged ? 'var(--color-score-fail, #cf222e)' : 'var(--color-score-pass, #1a7f37)'}
							<div class="flex justify-center">
								{#if cell}
									<span class="inline-flex items-center justify-center rounded-full px-2.5 py-0.5 text-xs font-semibold tabular-nums"
										style="background: color-mix(in srgb, {cellColor} 12%, transparent); color: {cellColor}">
										{cell.counts[1]}/{cell.n} flagged
									</span>
								{:else}
									<span class="text-xs text-text-muted">—</span>
								{/if}
							</div>
						{/each}
						</button>

						<!-- Expanded sample pairs -->
						{#if isExpanded}
							<div transition:slide={{ duration: 200, easing: quintOut }}>
								<div class="border-t border-border bg-surface-2/40 px-4 py-4 space-y-3">
									{#if matched.length === 0}
										<div class="text-xs text-text-muted text-center py-4">
											{disagreementsOnly ? 'No disagreements for this behavior' : 'No samples'}
										</div>
									{:else}
										{#each displaySamples as pair, pairIdx (pair.prompt + ':' + pairIdx)}
											<div class="rounded-lg border border-border bg-surface overflow-hidden">
												{#if activeKind === 'scenarios'}
													<!-- Opening (seed) turn — identical across runs; conversations
													     diverge from turn 2 onward as each variant is driven by the
													     adaptive tester. -->
													<div class="px-4 py-2.5 bg-surface-2 border-b border-border">
														<div class="text-[12px] font-medium text-text-muted mb-0.5">Opening prompt</div>
														<div class="text-sm text-text leading-relaxed">{(() => { const op = openingPrompt(pair.samples); return op.length > 200 ? op.slice(0, 200) + '…' : op; })()}</div>
													</div>

													<!-- Per-run multi-turn transcripts side by side -->
													<div class="overflow-x-auto">
														<div
															class="grid divide-x divide-border"
															style="grid-template-columns: {sampleGridTemplate(data.runs.length)}; min-width: {sampleGridMinWidth(data.runs.length)};"
														>
															{#each orderedRuns as run (run.run_id)}
																{@const sample = pair.samples[run.run_id]}
																{@const convo = conversationMessages(sample)}
																<div class="p-3 space-y-2">
																	<!-- Model + score -->
																	<div class="flex items-center justify-between">
																		<div class="flex items-center gap-1.5 min-w-0">
																			<span class="h-1.5 w-1.5 rounded-full flex-shrink-0" style="background: {runColor[run.run_id]}"></span>
																			<span class="text-xs font-mono truncate" title={run.model} style="color: {runColor[run.run_id]}">{runLabel(run.model)}</span>
																		</div>
																		{#if sample}
																			{@const sampleScore = getRecordFlag(sample, activeMetric)}
																			{#if sampleScore !== null}
																				<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold {scoreBadgeClass(sampleScore)}">
																					{sampleScore ? 'Flagged' : 'Clear'}
																				</span>
																			{:else if judgeStatus(sample) === 'judge_failed'}
																				<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-amber-500/10 text-amber-500">
																					Judge failed
																				</span>
																			{/if}
																		{/if}
																	</div>

																	{#if sample}
																		<div class="text-[10px] uppercase tracking-wide text-text-muted">
																			{convo.length} turn{convo.length === 1 ? '' : 's'}
																		</div>
																		<!-- Scrollable conversation; capped height so cards
																		     stay aligned and long transcripts don't blow up
																		     the page. -->
																		<div class="max-h-80 overflow-y-auto rounded border border-border bg-surface-2/40 p-2 space-y-2">
																			{#each convo as msg, msgIdx (msgIdx)}
																				{@const isUser = msg.role === 'user'}
																				{@const isAssistant = msg.role === 'assistant'}
																				<div class="text-xs leading-relaxed">
																					<div class="mb-0.5 font-semibold {isUser ? 'text-interactive' : isAssistant ? 'text-text' : 'text-text-muted'}">
																						{turnRoleLabel(msg.role)}
																					</div>
																					<div class="whitespace-pre-wrap text-text-secondary">{msg.content.length > 600 ? msg.content.slice(0, 600) + '…' : msg.content}</div>
																				</div>
																			{/each}
																			{#if convo.length === 0}
																				<p class="text-xs text-text-muted italic">No transcript captured</p>
																			{/if}
																		</div>
																		{#if typeof sample.verdict?.justification === 'string'}
																			<p class="text-xs text-text-muted italic leading-relaxed border-t border-border pt-2 mt-2">
																				{sample.verdict.justification.length > 200 ? sample.verdict.justification.slice(0, 200) + '…' : sample.verdict.justification}
																			</p>
																		{:else if judgeStatus(sample) === 'judge_failed'}
																			<p class="text-xs text-amber-500 italic leading-relaxed border-t border-border pt-2 mt-2">
																				Judge failed{getJudgeError(sample) ? `: ${getJudgeError(sample)}` : ''}
																			</p>
																		{/if}
																	{:else}
																		<p class="text-xs text-text-muted italic">No scenario for this run</p>
																	{/if}
																</div>
															{/each}
														</div>
													</div>
												{:else}
												<!-- Prompt -->
												<div class="px-4 py-2.5 bg-surface-2 border-b border-border">
													<div class="text-[12px] font-medium text-text-muted mb-0.5">Test prompt</div>
													<div class="text-sm text-text leading-relaxed">{pair.prompt.length > 200 ? pair.prompt.slice(0, 200) + '…' : pair.prompt}</div>
												</div>

												<!-- Responses side by side -->
												<div class="overflow-x-auto">
													<div
														class="grid divide-x divide-border"
														style="grid-template-columns: {sampleGridTemplate(data.runs.length)}; min-width: {sampleGridMinWidth(data.runs.length)};"
													>
														{#each orderedRuns as run (run.run_id)}
															{@const sample = pair.samples[run.run_id]}
															<div class="p-3 space-y-2">
														<!-- Model + score -->
														<div class="flex items-center justify-between">
															<div class="flex items-center gap-1.5 min-w-0">
																<span class="h-1.5 w-1.5 rounded-full flex-shrink-0" style="background: {runColor[run.run_id]}"></span>
																<span class="text-xs font-mono truncate" title={run.model} style="color: {runColor[run.run_id]}">{runLabel(run.model)}</span>
															</div>
															{#if sample}
																{@const sampleScore = getRecordFlag(sample, activeMetric)}
																{#if sampleScore !== null}
																	<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold {scoreBadgeClass(sampleScore)}">
																		{sampleScore ? 'Flagged' : 'Clear'}
																	</span>
																{:else if judgeStatus(sample) === 'judge_failed'}
																	<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-amber-500/10 text-amber-500">
																		Judge failed
																	</span>
																{/if}
															{/if}
														</div>

														<!-- Response text -->
														{#if sample}
															<p class="text-sm leading-relaxed text-text-secondary">
																{sample.response.length > 300 ? sample.response.slice(0, 300) + '…' : sample.response}
															</p>
															{#if typeof sample.verdict?.justification === 'string'}
																<p class="text-xs text-text-muted italic leading-relaxed border-t border-border pt-2 mt-2">
																	{sample.verdict.justification.length > 200 ? sample.verdict.justification.slice(0, 200) + '…' : sample.verdict.justification}
																</p>
															{:else if judgeStatus(sample) === 'judge_failed'}
																<p class="text-xs text-amber-500 italic leading-relaxed border-t border-border pt-2 mt-2">
																	Judge failed{getJudgeError(sample) ? `: ${getJudgeError(sample)}` : ''}
																</p>
															{/if}
														{:else}
															<p class="text-xs text-text-muted italic">No sample for this run</p>
														{/if}
															</div>
														{/each}
													</div>
												</div>
												{/if}
											</div>
										{/each}

										<!-- Show all toggle -->
										{#if matched.length > 3 && !showAll}
											<button
												type="button"
												class="btn btn-invisible btn-small w-full"
												onclick={() => { showAllMap[row.behavior] = true; }}
											>
												Show all {matched.length} samples
											</button>
										{/if}
									{/if}
								</div>
							</div>
						{/if}
					</div>
				{/each}

				{#if data.comparisons.length === 0}
					<div class="px-4 py-10 text-center text-sm text-text-muted">
						No behavior data to compare
					</div>
				{/if}
			</div>
		</div>
	</section>
</div>
{/if}
