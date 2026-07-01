<!--
  Copyright (c) Microsoft Corporation.
  Licensed under the MIT License.
-->
<script lang="ts">
	import type {
		AuditScore,
		BinaryCounts,
		DimensionDef,
		DimensionMetrics,
		JudgedSample,
		MultiJudge,
		ViewerResultItem
	} from '$lib/types.js';
	import {
		getRecordFlag,
		getRequiredBaseMetricNames,
		inferJudgeStatus,
		multiJudgeDimensionAgreementLabel,
		multiJudgeHasDisagreement,
		multiJudgeMeanAgreement
	} from '$lib/judgment.js';
	import ExportSeedDetail from './ExportSeedDetail.svelte';

	type MetricSummary = DimensionMetrics;

	type ExportPageData = {
		suite_id: string;
		run_id: string;
		manifest?: {
			status?: string;
			started_at?: string | null;
			ended_at?: string | null;
			stages?: Record<string, string>;
		} | null;
		taxonomy?: { behavior?: { name?: string } | null } | null;
		samples: JudgedSample[];
		auditScores: AuditScore[];
		promptCount?: number;
		auditCount?: number;
		hasAuditContent?: boolean;
		dimensionDefs?: Record<string, DimensionDef> | null;
		metrics: { dimensions?: Record<string, MetricSummary> } | null;
		auditMetrics: { dimensions?: Record<string, MetricSummary> } | null;
		promptSeedTitleMap?: Record<string, string>;
		scenarioSeedMap?: Record<string, { description?: string | null }>;
	};

	type MultiJudgeStats = {
		judgeSamples: number;
		meanAgreement: number;
		disagreements: number;
		failedSamples: number;
	};

	let {
		data,
		promptDrawerItems,
		scenarioDrawerItems,
		generatedAt
	}: {
		data: ExportPageData;
		promptDrawerItems: Record<string, ViewerResultItem>;
		scenarioDrawerItems: Record<string, ViewerResultItem>;
		generatedAt: string;
	} = $props();

	const RUN_STAGE_LABELS: Record<string, string> = {
		seeds: 'Seed Generation',
		inference: 'Inference',
		judge: 'Scoring'
	};

	const requiredBaseMetrics = $derived(
		getRequiredBaseMetricNames((data.dimensionDefs ?? {}) as Record<string, DimensionDef>)
	);

	function judgeStatus(record: {
		verdict?: Record<string, unknown> | null;
		judge_status?: string | null;
		judge_error?: string | null;
	}) {
		if (record.judge_status == null && record.judge_error == null && record.verdict == null) {
			return 'unjudged';
		}
		return inferJudgeStatus(record, requiredBaseMetrics);
	}

	function metricLabel(metric: string): string {
		return metric.replace(/_/g, ' ');
	}
	function metricOutcomeText(flag: boolean | null): string {
		if (flag === null) return 'n/a';
		return flag ? 'flagged' : 'clear';
	}
	function metricOutcomeClass(flag: boolean | null): string {
		if (flag === null) return 'text-text-muted';
		return flag ? 'text-score-fail' : 'text-score-pass';
	}
	function metricRateClass(rate: number | null): string {
		if (rate == null) return 'text-text-muted';
		if (rate >= 0.5) return 'text-score-fail';
		if (rate > 0) return 'text-score-border';
		return 'text-score-pass';
	}
	function metricRateText(rate: number | null): string {
		return rate == null ? 'N/A' : `${(rate * 100).toFixed(0)}%`;
	}
	function binaryBar(counts: BinaryCounts | undefined): { clear: number; flagged: number } {
		if (!counts) return { clear: 0, flagged: 0 };
		const total = (counts[0] ?? 0) + (counts[1] ?? 0);
		if (total === 0) return { clear: 0, flagged: 0 };
		return { clear: ((counts[0] ?? 0) / total) * 100, flagged: ((counts[1] ?? 0) / total) * 100 };
	}
	function metricDotColor(flag: boolean): string {
		return flag ? 'var(--color-score-fail)' : 'var(--color-score-pass)';
	}

	function sectionMultiJudgeStats(
		records: Array<{ multi_judge?: MultiJudge }>,
		metricNames: string[]
	): MultiJudgeStats | null {
		const withMj = records.filter((r) => r.multi_judge);
		if (withMj.length === 0) return null;
		const agreements = withMj.map((r) => {
			const value = multiJudgeMeanAgreement(r.multi_judge, metricNames);
			return value == null ? 1 : value;
		});
		const meanAgreement = agreements.reduce((sum, value) => sum + value, 0) / agreements.length;
		const disagreements = withMj.filter((r) =>
			multiJudgeHasDisagreement(r.multi_judge, metricNames)
		).length;
		const failedSamples = withMj.filter((r) => (r.multi_judge?.n_failed ?? 0) > 0).length;
		return { judgeSamples: withMj.length, meanAgreement, disagreements, failedSamples };
	}

	const hasPromptEval = $derived((data.promptCount ?? data.samples.length) > 0);
	const hasAuditEval = $derived((data.auditCount ?? data.auditScores.length) > 0);

	const promptDimensionNames = $derived(Object.keys(data.metrics?.dimensions ?? {}));
	const auditDimensionNames = $derived(Object.keys(data.auditMetrics?.dimensions ?? {}));
	const promptMetricNames = $derived(promptDimensionNames);
	const auditMetricNames = $derived(auditDimensionNames);
	const promptPrimaryMetric = $derived(promptMetricNames[0] ?? 'policy_violation');
	const auditPrimaryMetric = $derived(auditMetricNames[0] ?? 'policy_violation');

	const promptMultiJudgeStats = $derived(sectionMultiJudgeStats(data.samples, promptMetricNames));
	const auditMultiJudgeStats = $derived(sectionMultiJudgeStats(data.auditScores, auditMetricNames));

	const promptScored = $derived(data.samples.filter((s) => judgeStatus(s) === 'ok').length);
	const promptJudgeFailures = $derived(data.samples.length - promptScored);
	const auditScored = $derived(data.auditScores.filter((s) => judgeStatus(s) === 'ok').length);
	const auditJudgeFailures = $derived(data.auditScores.length - auditScored);

	const promptMetricCards = $derived(
		promptMetricNames.map((dim) => ({
			key: dim,
			name: metricLabel(dim),
			summary: data.metrics?.dimensions?.[dim],
			description: data.dimensionDefs?.[dim]?.description ?? ''
		}))
	);
	const auditMetricCards = $derived(
		auditMetricNames.map((dim) => ({
			key: dim,
			name: metricLabel(dim),
			summary: data.auditMetrics?.dimensions?.[dim],
			description: data.dimensionDefs?.[dim]?.description ?? ''
		}))
	);

	function durationLabel(start?: string | null, end?: string | null): string | null {
		if (!start || !end) return null;
		const ms = new Date(end).getTime() - new Date(start).getTime();
		if (!Number.isFinite(ms) || ms < 0) return null;
		const secs = ms / 1000;
		return `${Math.round(secs / 60)}m ${Math.round(secs % 60)}s`;
	}
	const runDuration = $derived(durationLabel(data.manifest?.started_at, data.manifest?.ended_at));
</script>

<!-- Header -->
<div class="mb-8">
	<div class="flex items-center gap-1.5 text-xs text-text-muted">
		<span>Evaluation suites</span>
		<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
		<span>{data.taxonomy?.behavior?.name ?? data.suite_id}</span>
		<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
		<span class="text-text-secondary">{data.run_id}</span>
	</div>
	<div class="mt-3 flex flex-wrap items-center gap-2.5">
		<h1 class="text-xl font-semibold tracking-tight">{data.run_id}</h1>
		{#if data.manifest?.status === 'completed' || hasPromptEval || hasAuditEval}
			<span class="inline-flex items-center gap-1 rounded-full bg-score-pass/10 px-2 py-0.5 text-xs text-score-pass">
				<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path d="M5 13l4 4L19 7"/></svg>
				Completed
			</span>
		{:else if data.manifest?.status === 'failed'}
			<span class="inline-flex items-center gap-1 rounded-full bg-score-fail/10 px-2 py-0.5 text-xs text-score-fail">
				<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path d="M6 18L18 6M6 6l12 12"/></svg>
				Failed
			</span>
		{/if}
		<span class="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-text-muted">{data.suite_id}/{data.run_id}</span>
		<span class="ml-auto rounded bg-surface px-2 py-0.5 text-[10px] text-text-muted" title="Time the export was generated">
			Exported {new Date(generatedAt).toLocaleString()}
		</span>
	</div>
	{#if data.manifest?.started_at}
		<p class="mt-2 text-xs text-text-muted">
			Started {new Date(data.manifest.started_at).toLocaleString()}{#if runDuration} · {runDuration}{/if}
		</p>
	{/if}

	{#if data.manifest?.stages}
		<div class="mt-3 max-w-2xl rounded-lg border border-border bg-surface p-4">
			<h3 class="mb-3 text-xs font-semibold uppercase tracking-wider text-text-muted">Evaluation pipeline</h3>
			<div class="grid gap-2">
				{#each Object.entries(data.manifest.stages) as [stage, info]}
					<div class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-border bg-background px-3 py-2">
						<span class="inline-flex h-5 w-5 items-center justify-center rounded-full text-xs {info === 'completed' ? 'bg-score-pass/10 text-score-pass' : info === 'failed' ? 'bg-score-fail/10 text-score-fail' : 'bg-surface-2 text-text-muted'}">
							{info === 'completed' ? '✓' : info === 'failed' ? '✗' : '○'}
						</span>
						<span class="text-xs font-medium text-text-secondary">{RUN_STAGE_LABELS[stage] ?? stage}</span>
						<span class="ml-auto text-xs text-text-muted">{info}</span>
					</div>
				{/each}
			</div>
		</div>
	{/if}
</div>

{#if !hasPromptEval && !hasAuditEval}
	<div class="rounded-lg border border-border bg-surface px-6 py-12 text-center">
		<p class="text-sm text-text-secondary">No measurement results in this run.</p>
	</div>
{/if}

<!-- ==================== PROMPT EVAL SECTION ==================== -->
{#if hasPromptEval}
	<section class="mb-12">
		<div class="mb-4 flex items-center gap-3">
			<h2 class="text-sm font-semibold uppercase tracking-widest text-text-muted">Prompts</h2>
			<div class="h-px flex-1 bg-border"></div>
			<span class="text-xs text-text-muted">{data.samples.length} samples</span>
		</div>

		<!-- Metric cards -->
		<div class="mb-4 grid gap-3" style="grid-template-columns: repeat({Math.min(promptMetricCards.length, 4)}, minmax(0, 1fr))">
			{#each promptMetricCards as m}
				{@const pct = binaryBar(m.summary?.counts ?? { 0: 0, 1: 0 } as BinaryCounts)}
				<div class="rounded-lg border border-border bg-surface px-5 py-4">
					<div class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">{m.name}</div>
					{#if m.description}
						<p class="mt-0.5 text-[10px] text-text-muted/60 leading-snug line-clamp-2">{m.description}</p>
					{/if}
					<div class="mt-2 flex items-baseline gap-1.5">
						<span class="text-3xl font-bold tabular-nums {metricRateClass(m.summary?.rate ?? null)}">{metricRateText(m.summary?.rate ?? null)}</span>
						<span class="text-sm text-text-muted">flagged</span>
					</div>
					{#if (m.summary?.count ?? 0) > 0}
						<div class="mt-2.5 flex h-1.5 overflow-hidden rounded-full bg-border/50">
							{#if pct.clear > 0}
								<div class="bg-score-pass" style="width: {pct.clear}%"></div>
							{/if}
							{#if pct.flagged > 0}
								<div class="bg-score-fail" style="width: {pct.flagged}%"></div>
							{/if}
						</div>
						<div class="mt-1 flex justify-between text-[9px] tabular-nums text-text-muted">
							<span>{m.summary?.clear_count ?? 0} clear</span>
							<span>{m.summary?.flagged_count ?? 0} flagged</span>
							<span>{m.summary?.count ?? 0} total</span>
						</div>
					{/if}
					{#if promptMultiJudgeStats}
						<div class="mt-2 text-[9px] text-text-muted">aggregate uses majority judge vote</div>
					{/if}
				</div>
			{/each}
		</div>

		{#if promptMultiJudgeStats}
			<div class="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-surface px-4 py-2 text-[10px] text-text-muted">
				<span class="font-semibold text-text-secondary">{promptMultiJudgeStats.judgeSamples} judge samples</span>
				<span>{(promptMultiJudgeStats.meanAgreement * 100).toFixed(0)}% mean agreement</span>
				<span>{promptMultiJudgeStats.disagreements} disagreement{promptMultiJudgeStats.disagreements === 1 ? '' : 's'}</span>
				{#if promptMultiJudgeStats.failedSamples > 0}
					<span class="text-amber-400">{promptMultiJudgeStats.failedSamples} failed sample{promptMultiJudgeStats.failedSamples === 1 ? '' : 's'}</span>
				{/if}
			</div>
		{/if}

		{#if promptJudgeFailures > 0}
			<p class="mb-4 text-xs text-amber-400">
				Scored {promptScored} of {data.samples.length} prompts. {promptJudgeFailures} judge failures were excluded from the rates.
			</p>
		{/if}

		<!-- Flat row list, each row's full drawer body inside <details> -->
		<div class="overflow-hidden rounded-lg border border-border">
			{#each data.samples as sample, sIdx}
				{@const drawerItem = sample.test_case_id ? promptDrawerItems[sample.test_case_id] : undefined}
				{@const status = judgeStatus(sample)}
				<details class="{sIdx > 0 ? 'border-t border-border/50' : ''} group">
					<summary class="flex cursor-pointer list-none items-center gap-3 px-5 py-2.5 transition-colors hover:bg-surface/50">
						<svg class="h-3 w-3 shrink-0 text-text-muted/60 transition-transform group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
						<span class="flex-1 truncate text-sm text-text-secondary">{(sample.test_case_id && data.promptSeedTitleMap?.[sample.test_case_id]) || sample.prompt}</span>
						<div class="flex shrink-0 items-center gap-1.5">
							{#each promptMetricNames as m}
								{@const v = getRecordFlag(sample, m)}
								{#if v !== null}
									<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]">
										<span class="text-text-muted">{metricLabel(m)}</span>
										<span class="font-semibold tabular-nums {metricOutcomeClass(v)}">{metricOutcomeText(v)}</span>
									</span>
								{/if}
							{/each}
							{#if status === 'judge_failed'}
								<span class="inline-flex items-center rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">judge failed</span>
							{/if}
							{#if sample.multi_judge}
								<div class="ml-1 flex items-center gap-0.5">
									{#each (sample.multi_judge as MultiJudge).votes?.[promptPrimaryMetric] ?? [] as vote}
										{@const agreed = vote === getRecordFlag(sample, promptPrimaryMetric)}
										<span
											class="inline-block size-[6px] rounded-full"
											style={agreed ? `background: ${metricDotColor(vote)}` : `background: transparent; box-shadow: inset 0 0 0 1.5px ${metricDotColor(vote)}`}
											title={metricOutcomeText(vote)}
										></span>
									{/each}
								</div>
								{#if multiJudgeHasDisagreement(sample.multi_judge as MultiJudge, [promptPrimaryMetric])}
									<span class="text-[10px] tabular-nums text-text-muted">{multiJudgeDimensionAgreementLabel(sample.multi_judge as MultiJudge, promptPrimaryMetric)}</span>
								{/if}
							{/if}
						</div>
					</summary>
					{#if drawerItem}
						<div class="border-t border-border/50 bg-bg/50 px-5 py-4">
							<ExportSeedDetail item={drawerItem} metricNames={promptMetricNames} primaryMetric={promptPrimaryMetric} />
						</div>
					{:else}
						<div class="border-t border-border/50 bg-bg/50 px-5 py-3 text-xs text-text-muted">
							(no detail loaded for test_case_id={sample.test_case_id ?? '?'})
						</div>
					{/if}
				</details>
			{/each}
		</div>
	</section>
{/if}

<!-- ==================== AUDIT EVAL SECTION ==================== -->
{#if hasAuditEval}
	<section class="mb-12">
		<div class="mb-4 flex items-center gap-3">
			<h2 class="text-sm font-semibold uppercase tracking-widest text-text-muted">Scenarios</h2>
			<div class="h-px flex-1 bg-border"></div>
			<span class="text-xs text-text-muted">{data.auditScores.length} scenarios</span>
		</div>

		<div class="mb-4 grid gap-3" style="grid-template-columns: repeat({Math.min(auditMetricCards.length, 4)}, minmax(0, 1fr))">
			{#each auditMetricCards as m}
				{@const pct = binaryBar(m.summary?.counts ?? { 0: 0, 1: 0 } as BinaryCounts)}
				<div class="rounded-lg border border-border bg-surface px-5 py-4">
					<div class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">{m.name}</div>
					{#if m.description}
						<p class="mt-0.5 text-[10px] text-text-muted/60 leading-snug line-clamp-2">{m.description}</p>
					{/if}
					<div class="mt-2 flex items-baseline gap-1.5">
						<span class="text-3xl font-bold tabular-nums {metricRateClass(m.summary?.rate ?? null)}">{metricRateText(m.summary?.rate ?? null)}</span>
						<span class="text-sm text-text-muted">flagged</span>
					</div>
					{#if (m.summary?.count ?? 0) > 0}
						<div class="mt-2.5 flex h-1.5 overflow-hidden rounded-full bg-border/50">
							{#if pct.clear > 0}<div class="bg-score-pass" style="width: {pct.clear}%"></div>{/if}
							{#if pct.flagged > 0}<div class="bg-score-fail" style="width: {pct.flagged}%"></div>{/if}
						</div>
						<div class="mt-1 flex justify-between text-[9px] tabular-nums text-text-muted">
							<span>{m.summary?.clear_count ?? 0} clear</span>
							<span>{m.summary?.flagged_count ?? 0} flagged</span>
							<span>{m.summary?.count ?? 0} total</span>
						</div>
					{/if}
				</div>
			{/each}
		</div>

		{#if auditMultiJudgeStats}
			<div class="mb-4 flex flex-wrap items-center gap-2 rounded-lg border border-border bg-surface px-4 py-2 text-[10px] text-text-muted">
				<span class="font-semibold text-text-secondary">{auditMultiJudgeStats.judgeSamples} judge samples</span>
				<span>{(auditMultiJudgeStats.meanAgreement * 100).toFixed(0)}% mean agreement</span>
				<span>{auditMultiJudgeStats.disagreements} disagreement{auditMultiJudgeStats.disagreements === 1 ? '' : 's'}</span>
				{#if auditMultiJudgeStats.failedSamples > 0}
					<span class="text-amber-400">{auditMultiJudgeStats.failedSamples} failed sample{auditMultiJudgeStats.failedSamples === 1 ? '' : 's'}</span>
				{/if}
			</div>
		{/if}

		{#if auditJudgeFailures > 0}
			<p class="mb-4 text-xs text-amber-400">
				Scored {auditScored} of {data.auditScores.length} scenarios. {auditJudgeFailures} judge failures were excluded.
			</p>
		{/if}

		<div class="overflow-hidden rounded-lg border border-border">
			{#each data.auditScores as score, sIdx}
				{@const drawerItem = score.test_case_id ? scenarioDrawerItems[score.test_case_id] : undefined}
				{@const status = judgeStatus(score)}
				{@const title = (score.test_case_id && data.scenarioSeedMap?.[score.test_case_id]?.description) || score.test_case_id || ''}
				<details class="{sIdx > 0 ? 'border-t border-border/50' : ''} group">
					<summary class="flex cursor-pointer list-none items-center gap-3 px-5 py-2.5 transition-colors hover:bg-surface/50">
						<svg class="h-3 w-3 shrink-0 text-text-muted/60 transition-transform group-open:rotate-90" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
						<span class="flex-1 truncate text-sm text-text-secondary">{title}</span>
						<div class="flex shrink-0 items-center gap-1.5">
							{#each auditMetricNames as m}
								{@const v = getRecordFlag(score, m)}
								{#if v !== null}
									<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]">
										<span class="text-text-muted">{metricLabel(m)}</span>
										<span class="font-semibold tabular-nums {metricOutcomeClass(v)}">{metricOutcomeText(v)}</span>
									</span>
								{/if}
							{/each}
							{#if status === 'judge_failed'}
								<span class="inline-flex items-center rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">judge failed</span>
							{/if}
							{#if score.multi_judge}
								<div class="ml-1 flex items-center gap-0.5">
									{#each (score.multi_judge as MultiJudge).votes?.[auditPrimaryMetric] ?? [] as vote}
										{@const agreed = vote === getRecordFlag(score, auditPrimaryMetric)}
										<span
											class="inline-block size-[6px] rounded-full"
											style={agreed ? `background: ${metricDotColor(vote)}` : `background: transparent; box-shadow: inset 0 0 0 1.5px ${metricDotColor(vote)}`}
											title={metricOutcomeText(vote)}
										></span>
									{/each}
								</div>
								{#if multiJudgeHasDisagreement(score.multi_judge as MultiJudge, [auditPrimaryMetric])}
									<span class="text-[10px] tabular-nums text-text-muted">{multiJudgeDimensionAgreementLabel(score.multi_judge as MultiJudge, auditPrimaryMetric)}</span>
								{/if}
							{/if}
						</div>
					</summary>
					{#if drawerItem}
						<div class="border-t border-border/50 bg-bg/50 px-5 py-4">
							<ExportSeedDetail item={drawerItem} metricNames={auditMetricNames} primaryMetric={auditPrimaryMetric} />
						</div>
					{:else}
						<div class="border-t border-border/50 bg-bg/50 px-5 py-3 text-xs text-text-muted">
							(no detail loaded for test_case_id={score.test_case_id ?? '?'})
						</div>
					{/if}
				</details>
			{/each}
		</div>
	</section>
{/if}

<footer class="mt-12 border-t border-border pt-6 text-center text-[10px] text-text-muted">
	Generated by assert-ai viewer · {new Date(generatedAt).toLocaleString()}
</footer>
