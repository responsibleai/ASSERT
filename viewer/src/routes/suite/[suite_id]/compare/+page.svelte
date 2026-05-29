<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import { getJudgeError, getRecordFlag, getRequiredBaseMetricNames, inferJudgeStatus } from '$lib/judgment.js';
	import { buildMatchedSampleRows } from '$lib/compare-view.js';
	import PrimerDropdown from '$lib/PrimerDropdown.svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';
	import type { BinaryCounts, DimensionDef } from '$lib/types.js';

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

// Expanded behavior rows
let expandedRows = $state<Set<string>>(new Set());

// "Disagreements only" filter
let disagreementsOnly = $state(false);

// Active metric for comparison
let activeMetric = $state('policy_violation');

function metricLabel(m: string): string {
	return m.replace(/_/g, ' ');
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

function getMatchedSamples(behavior: string) {
	return buildMatchedSampleRows(
		data.samplesByBehavior[behavior] ?? {},
		runIds,
		activeMetric,
		disagreementsOnly
	);
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

		<div class="mt-4">
			<!-- Baseline dropdown -->
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
	</div>
</div>

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
			{#each data.runs as run, i}
				{@const baseline = data.runs[baselineIdx]}
				{@const avg = activeMetric === 'policy_violation' ? run.policyViolationRate : (run.dimensions[activeMetric]?.rate ?? 0)}
				{@const baselineAvg = activeMetric === 'policy_violation' ? baseline.policyViolationRate : (baseline.dimensions[activeMetric]?.rate ?? 0)}
				{@const delta = i !== baselineIdx ? avg - baselineAvg : 0}
				{@const runScores = activeMetric === 'policy_violation' ? run.counts : (run.dimensions[activeMetric]?.counts ?? { 0: 0, 1: 0 })}
				{@const pct = pctBar(runScores)}
				{@const totalSamples = runScores[0] + runScores[1]}
				<div class="rounded-lg border border-border bg-surface px-5 py-4">
					<!-- Header: run name + sample count -->
					<div class="flex items-start justify-between gap-3">
						<div class="flex items-center gap-2 min-w-0">
							<span class="h-2 w-2 rounded-full flex-shrink-0" style="background: {RUN_COLORS[i]}"></span>
							<span class="text-sm font-medium text-text truncate">{run.display_name}</span>
							{#if i === baselineIdx}
								<span class="inline-flex items-center rounded-full bg-interactive/15 px-2 py-0.5 text-[10px] font-semibold text-interactive ring-1 ring-interactive/40">Baseline</span>
							{/if}
						</div>
						<span class="shrink-0 text-[12px] text-text-muted tabular-nums">{run.total} samples</span>
					</div>

					<!-- Model name -->
					<div class="mt-1 font-mono text-xs text-text-muted truncate">{run.model}</div>

					<!-- Big number -->
					<div class="mt-3 flex items-baseline gap-1.5">
						<span class="text-3xl font-bold tabular-nums text-text">{(avg * 100).toFixed(0)}%</span>
						<span class="text-sm text-text-muted">Flagged</span>
						{#if i !== baselineIdx && Math.abs(delta) >= 0.005}
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
					{#each data.runs as run, i}
						<span class="text-center font-mono font-medium" style="color: {RUN_COLORS[i]}">{run.model.split('/').pop()}</span>
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
						{#each data.runs as run, i}
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
										{#each displaySamples as pair, pairIdx (pair.prompt)}
											<div class="rounded-lg border border-border bg-surface overflow-hidden">
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
														{#each data.runs as run, i}
															{@const sample = pair.samples[run.run_id]}
															<div class="p-3 space-y-2">
														<!-- Model + score -->
														<div class="flex items-center justify-between">
															<div class="flex items-center gap-1.5 min-w-0">
																<span class="h-1.5 w-1.5 rounded-full flex-shrink-0" style="background: {RUN_COLORS[i]}"></span>
																<span class="text-xs font-mono truncate" style="color: {RUN_COLORS[i]}">{run.model.split('/').pop()}</span>
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
