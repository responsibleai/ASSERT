<script lang="ts">
	import { getJudgeError, getRecordFlag, getRequiredBaseMetricNames, inferJudgeStatus } from '$lib/judgment.js';
	import { buildMatchedSampleRows } from '$lib/compare-view.js';
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
	return `minmax(14rem, 1fr) repeat(${runCount}, 100px) 80px`;
}

function comparisonTableMinWidth(runCount: number): string {
	return `${14 + runCount * 6.25 + 5}rem`;
}

function sampleGridTemplate(runCount: number): string {
	return `repeat(${runCount}, minmax(16rem, 1fr))`;
}

function sampleGridMinWidth(runCount: number): string {
	return `${runCount * 16}rem`;
}
</script>

<!-- Back link -->
<div class="mx-auto max-w-5xl px-6 pt-6">
	<a href="/suite/{data.suite_id}?section=results" class="inline-flex items-center gap-1.5 text-xs text-text-muted hover:text-interactive transition-colors">
		<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7"/></svg>
		Back to {data.suite_id}
	</a>
</div>

<div class="mx-auto max-w-5xl px-6 pt-4 pb-24 space-y-12">

	<!-- ═══ SECTION 1: Header ═══ -->
	<header class="space-y-5">
		<h1 class="text-lg font-semibold text-text">
			Comparing {data.runs.length} runs
			<span class="text-text-muted font-normal">on</span>
			<span class="text-interactive">{data.policy?.concept?.name ?? data.suite_id}</span>
		</h1>

		<div class="flex flex-wrap gap-3">
			{#each data.runs as run, i}
				<button
					onclick={() => { baselineIdx = i; }}
					class="group relative flex items-center gap-3 rounded-xl border px-4 py-3 transition-all duration-150
						{i === baselineIdx
							? 'border-white/20 bg-white/[0.04] shadow-[0_0_0_1px_rgba(255,255,255,0.06)]'
							: 'border-border bg-surface hover:border-white/10 hover:bg-white/[0.02]'}"
				>
					<!-- Color dot -->
					<span class="h-3 w-3 rounded-full flex-shrink-0" style="background: {RUN_COLORS[i]}"></span>

					<div class="text-left">
						<div class="text-sm font-medium" style="color: {RUN_COLORS[i]}">{run.display_name}</div>
						<div class="text-[10px] text-text-muted mt-0.5">{run.run_id} · {run.date}</div>
					</div>

					{#if i === baselineIdx}
						<span class="ml-1 rounded-full bg-white/10 px-2 py-0.5 text-[9px] font-semibold uppercase tracking-wider text-text-muted">
							baseline
						</span>
					{/if}
				</button>
			{/each}
		</div>
	</header>

	<!-- ═══ SECTION 2: Metric Picker + Summary Cards ═══ -->
	<section class="space-y-3">
		<!-- Metric picker -->
		{#if data.allMetrics.length > 1}
			<div class="flex items-center gap-1.5">
				{#each data.allMetrics as metric}
					<button
						onclick={() => { activeMetric = metric; }}
						class="rounded-lg px-3 py-1.5 text-xs font-medium transition-colors duration-150
							{activeMetric === metric
								? 'bg-interactive text-white'
								: 'text-text-muted hover:text-text hover:bg-surface-2'}"
					>
						{metricLabel(metric)}
					</button>
				{/each}
			</div>
		{:else}
			<h2 class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">{metricLabel(activeMetric)}</h2>
		{/if}

		<div class="grid gap-4" style="grid-template-columns: {summaryGridTemplate()};">
			{#each data.runs as run, i}
				{@const baseline = data.runs[baselineIdx]}
				{@const avg = activeMetric === 'policy_violation' ? run.policyViolationRate : (run.dimensions[activeMetric]?.rate ?? 0)}
				{@const baselineAvg = activeMetric === 'policy_violation' ? baseline.policyViolationRate : (baseline.dimensions[activeMetric]?.rate ?? 0)}
				{@const delta = i !== baselineIdx ? avg - baselineAvg : 0}
				{@const runScores = activeMetric === 'policy_violation' ? run.counts : (run.dimensions[activeMetric]?.counts ?? { 0: 0, 1: 0 })}
				{@const pct = pctBar(runScores)}
				<div class="rounded-xl border border-border bg-surface p-5 space-y-4">
					<!-- Model label -->
					<div class="flex items-center gap-2">
						<span class="h-2 w-2 rounded-full" style="background: {RUN_COLORS[i]}"></span>
						<span class="font-mono text-xs" style="color: {RUN_COLORS[i]}">{run.model}</span>
					</div>

					<!-- Big number -->
					<div class="flex items-end gap-2">
						<span class="text-3xl font-bold tabular-nums {rateTextClass(avg)}">{(avg * 100).toFixed(0)}%</span>
						{#if i !== baselineIdx}
							<span class="mb-1 text-sm font-semibold tabular-nums {deltaClass(delta)}">
								{deltaText(delta)} {deltaArrow(delta)}
							</span>
						{/if}
					</div>

					<!-- Pass rate -->
					<div class="space-y-1.5">
						<div class="flex justify-between text-[10px]">
							<span class="text-text-muted">Flagged rate</span>
							<span class="font-semibold tabular-nums text-text-secondary">{(avg * 100).toFixed(0)}%</span>
						</div>

						<!-- Score distribution bar -->
						<div class="flex h-2 w-full overflow-hidden rounded-full bg-surface-2">
							{#if pct.clear > 0}
								<div class="h-full bg-score-pass transition-all duration-300" style="width: {pct.clear}%"></div>
							{/if}
							{#if pct.flagged > 0}
								<div class="h-full bg-score-fail transition-all duration-300" style="width: {pct.flagged}%"></div>
							{/if}
						</div>

						<!-- Legend -->
						<div class="flex gap-3 text-[9px] text-text-muted">
							<span><span class="inline-block h-1.5 w-1.5 rounded-full bg-score-pass mr-0.5"></span> {runScores[0]} clear</span>
							<span><span class="inline-block h-1.5 w-1.5 rounded-full bg-score-fail mr-0.5"></span> {runScores[1]} flagged</span>
						</div>
					</div>

					<div class="text-[10px] text-text-muted">
						{#if run.judgeFailures > 0}
							{run.scoredTotal} scored / {run.total} total · {run.judgeFailures} judge failures · judge: <span class="font-mono">{run.judge_model}</span>
						{:else}
							{run.total} samples · judge: <span class="font-mono">{run.judge_model}</span>
						{/if}
					</div>

					<!-- Multi-judge agreement (only when data exists) -->
					{#if run.meanAgreement !== null}
						<div class="space-y-1 pt-2 border-t border-border/30">
							<div class="flex justify-between text-[10px]">
								<span class="text-text-muted">Judge agreement</span>
								<span class="font-semibold tabular-nums {run.meanAgreement >= 0.8 ? 'text-score-pass' : run.meanAgreement >= 0.6 ? 'text-score-border' : 'text-score-fail'}">{(run.meanAgreement * 100).toFixed(0)}%</span>
							</div>
							{#if run.highVarianceCount > 0}
								<div class="text-[9px] text-amber-500/70">{run.highVarianceCount} sample{run.highVarianceCount > 1 ? 's' : ''} with high variance</div>
							{/if}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	</section>

	<!-- ═══ SECTION 3: Behavior Heatmap ═══ -->
	<section class="space-y-3">
		<div class="flex items-center justify-between">
			<h2 class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">By Behavior</h2>

			<!-- Disagreements toggle -->
			<label class="flex items-center gap-2 cursor-pointer select-none">
				<span class="text-[11px] text-text-muted">Disagreements only</span>
				<button
					role="switch"
					aria-checked={disagreementsOnly}
					aria-label="Show disagreements only"
					onclick={() => { disagreementsOnly = !disagreementsOnly; }}
					class="relative h-5 w-9 rounded-full transition-colors duration-150 {disagreementsOnly ? 'bg-interactive' : 'bg-surface-2'}"
				>
					<span class="absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform duration-150 {disagreementsOnly ? 'translate-x-4' : ''}"></span>
				</button>
			</label>
		</div>

		<!-- Table header -->
		<div class="overflow-x-auto rounded-xl border border-border">
			<div class="min-w-max" style="min-width: {comparisonTableMinWidth(data.runs.length)};">
				<div class="grid items-center gap-2 px-4 py-2.5 bg-surface text-[10px] font-semibold uppercase tracking-wider text-text-muted border-b border-border"
					style="grid-template-columns: {comparisonGridTemplate(data.runs.length)};">
					<span>Behavior</span>
					{#each data.runs as run, i}
						<span class="text-center" style="color: {RUN_COLORS[i]}">{run.model.split('/').pop()}</span>
					{/each}
					<span class="text-right">Delta</span>
				</div>

				<!-- Rows -->
				{#each sortedComparisons as row (row.behavior)}
					{@const isExpanded = expandedRows.has(row.behavior)}
					{@const matched = isExpanded ? getMatchedSamples(row.behavior) : []}
					{@const showAll = showAllMap[row.behavior] ?? false}
					{@const displaySamples = showAll ? matched : matched.slice(0, 3)}
					{@const rowDelta = row.deltas[activeMetric] ?? 0}

					<div class="border-b border-border/50 last:border-b-0">
						<!-- Row -->
						<button
							onclick={() => toggleRow(row.behavior)}
							class="w-full grid items-center gap-2 px-4 py-3 text-left transition-colors duration-150 hover:bg-white/[0.02] cursor-pointer"
							style="grid-template-columns: {comparisonGridTemplate(data.runs.length)};"
						>
						<!-- Behavior name -->
						<div class="flex items-center gap-2 min-w-0">
							<svg class="h-3 w-3 flex-shrink-0 text-text-muted transition-transform duration-150 {isExpanded ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
								<path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/>
							</svg>
							<span class="text-xs text-text-secondary truncate">{row.behavior}</span>
						</div>

						<!-- Score cells -->
						{#each data.runs as run, i}
							{@const cell = row.metrics[activeMetric]?.[run.run_id]}
							<div class="flex justify-center">
								{#if cell}
									<span class="inline-flex items-center justify-center rounded-lg px-2.5 py-1 text-xs font-semibold tabular-nums min-w-[52px]"
										style="background: color-mix(in srgb, {rateColor(cell.rate)} 12%, transparent); color: {rateColor(cell.rate)}">
										{(cell.rate * 100).toFixed(0)}%
									</span>
								{:else}
									<span class="text-[10px] text-text-muted">—</span>
								{/if}
							</div>
						{/each}

						<!-- Delta -->
						<div class="text-right">
							<span class="text-xs font-semibold tabular-nums {deltaClass(rowDelta)}">
								{deltaText(rowDelta)} {deltaArrow(rowDelta)}
							</span>
						</div>
						</button>

						<!-- Expanded sample pairs -->
						{#if isExpanded}
							<div transition:slide={{ duration: 200, easing: quintOut }}>
								<div class="border-t border-border/30 bg-white/[0.01] px-4 py-4 space-y-3">
									{#if matched.length === 0}
										<div class="text-xs text-text-muted text-center py-4">
											{disagreementsOnly ? 'No disagreements for this behavior' : 'No samples'}
										</div>
									{:else}
										{#each displaySamples as pair, pairIdx (pair.prompt)}
											<div class="rounded-lg border border-border/50 overflow-hidden">
												<!-- Prompt -->
												<div class="px-4 py-2.5 bg-surface-2/30 border-b border-border/30">
													<span class="text-[10px] font-semibold uppercase tracking-wider text-text-muted mr-2">Prompt</span>
													<span class="text-xs text-text-secondary">{pair.prompt.length > 200 ? pair.prompt.slice(0, 200) + '…' : pair.prompt}</span>
												</div>

												<!-- Responses side by side -->
												<div class="overflow-x-auto">
													<div
														class="grid divide-x divide-border/30"
														style="grid-template-columns: {sampleGridTemplate(data.runs.length)}; min-width: {sampleGridMinWidth(data.runs.length)};"
													>
														{#each data.runs as run, i}
															{@const sample = pair.samples[run.run_id]}
															<div class="p-3 space-y-2">
														<!-- Model + score -->
														<div class="flex items-center justify-between">
															<div class="flex items-center gap-1.5">
																<span class="h-1.5 w-1.5 rounded-full" style="background: {RUN_COLORS[i]}"></span>
																<span class="text-[10px] font-mono" style="color: {RUN_COLORS[i]}">{run.model.split('/').pop()}</span>
															</div>
															{#if sample}
																{@const sampleScore = getRecordFlag(sample, activeMetric)}
																{#if sampleScore !== null}
																	<span class="rounded px-1.5 py-0.5 text-[10px] font-bold tabular-nums {scoreBadgeClass(sampleScore)}">
																		{sampleScore ? 'flagged' : 'clear'}
																	</span>
																{:else if judgeStatus(sample) === 'judge_failed'}
																	<span class="rounded px-1.5 py-0.5 text-[10px] font-medium bg-amber-500/10 text-amber-400">
																		judge failed
																	</span>
																{/if}
															{/if}
														</div>

														<!-- Response text -->
														{#if sample}
															<p class="text-[11px] leading-relaxed text-text-secondary">
																{sample.response.length > 300 ? sample.response.slice(0, 300) + '…' : sample.response}
															</p>
															{#if typeof sample.verdict?.justification === 'string'}
																<p class="text-[10px] text-text-muted italic leading-relaxed border-t border-border/20 pt-2 mt-2">
																	{sample.verdict.justification.length > 200 ? sample.verdict.justification.slice(0, 200) + '…' : sample.verdict.justification}
																</p>
															{:else if judgeStatus(sample) === 'judge_failed'}
																<p class="text-[10px] text-amber-400 italic leading-relaxed border-t border-border/20 pt-2 mt-2">
																	Judge failed{getJudgeError(sample) ? `: ${getJudgeError(sample)}` : ''}
																</p>
															{/if}
														{:else}
															<p class="text-[10px] text-text-muted italic">No sample for this run</p>
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
												onclick={() => { showAllMap[row.behavior] = true; }}
												class="w-full rounded-lg border border-border/30 py-2 text-[11px] text-text-muted hover:text-interactive hover:border-interactive/30 transition-colors"
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
					<div class="px-4 py-10 text-center text-xs text-text-muted">
						No behavior data to compare
					</div>
				{/if}
			</div>
		</div>
	</section>
</div>
