<script lang="ts">
	import type {
		JudgedSample,
		AuditScore,
		BinaryCounts,
		DimensionDef,
		GroupAxis,
		MultiJudge,
		ViewerResultItem
	} from '$lib/types.js';
	import { AUDIT_GROUP_AXES, PROMPT_GROUP_AXES, buildFactorAxes, groupByAxis } from '$lib/grouping.js';
	import ResultDrawer from '$lib/ResultDrawer.svelte';
	import { normalizePromptResult } from '$lib/result-view.js';
	import {
		getRecordFlag,
		getRequiredBaseMetricNames,
		inferJudgeStatus,
		scoreSortValue
	} from '$lib/judgment.js';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';

	let { data } = $props();
	let requiredBaseMetrics = $derived(
		getRequiredBaseMetricNames(data.dimensionDefs as Record<string, DimensionDef>)
	);

	type InferencePreviewItem = {
		test_case_id: string;
		behavior: string;
		turns_count: number;
		stop_reason: string;
	};

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

	// --- Tab state ---
	let hasPromptEval = $derived((data.promptCount ?? data.samples.length) > 0);
	let hasAuditEval = $derived((data.auditCount ?? data.auditScores.length) > 0);
	let hasAuditPreview = $derived((data.inferencePreviewRows?.length ?? 0) > 0);
	let hasAuditContent = $derived(data.hasAuditContent ?? (hasAuditEval || hasAuditPreview));
	let activeTab = $derived((data.activeTab ?? (page.url.searchParams.get('tab') === 'audit' ? 'audit' : 'prompts')) as 'prompts' | 'audit');

	function setActiveTab(tab: 'prompts' | 'audit') {
		if (tab === activeTab) return;
		const url = new URL(page.url);
		if (tab === 'audit') url.searchParams.set('tab', 'audit');
		else url.searchParams.delete('tab');
		goto(url.toString(), { replaceState: true, noScroll: true, keepFocus: true });
	}

	// --- Prompt eval state ---
	let expandedBehavior = $state<string | null>(null);
	let drawerSample = $state<JudgedSample | null>(null);
	let promptGroupBy = $state('none');
	let promptSortMetric = $state('policy_violation');
	let promptSearchQuery = $state('');

	// --- Audit eval state ---
	let expandedAuditBehavior = $state<string | null>(null);
	let drawerAuditScore = $state<AuditScore | null>(null);
	let drawerPreviewSeedId = $state<string | null>(null);
	let auditGroupBy = $state('none');
	let auditSortMetric = $state('policy_violation');
	let auditSearchQuery = $state('');
	let runMetaOpen = $state(false);

	// --- Multi-judge state ---
	let mjFilter = $state<'all' | 'disagreements'>('all');
	let auditMjFilter = $state<'all' | 'disagreements'>('all');

	// Totals for judge failures banner
	let promptTotal = $derived(data.samples.length);
	let promptScored = $derived(data.samples.filter(s => judgeStatus(s) === 'ok').length);
	let promptJudgeFailures = $derived(promptTotal - promptScored);

	let auditTotal = $derived(data.auditScores.length);
	let auditScored = $derived(data.auditScores.filter(s => judgeStatus(s) === 'ok').length);
	let auditJudgeFailures = $derived(auditTotal - auditScored);

	function metricLabel(metric: string): string {
		return metric.replace(/_/g, ' ');
	}

	function metricOutcomeText(flag: boolean | null): string {
		if (flag === null) return 'n/a';
		return flag ? 'flagged' : 'Pass';
	}

	function metricOutcomeClass(flag: boolean | null): string {
		if (flag === null) return 'text-text-muted';
		return flag ? 'text-score-fail' : 'text-score-pass';
	}

	function metricDotColor(flag: boolean): string {
		return flag ? 'var(--theme-score-fail)' : 'var(--theme-score-pass)';
	}

	function getBehaviorViolated(score: { verdict?: Record<string, unknown> | null }, behaviorName: string): boolean | null {
		const nj = score.verdict?.node_judgments;
		if (!Array.isArray(nj)) return null;
		for (const node of nj) {
			const name = ((node as Record<string, unknown>).node_name as string ?? '').trim();
			const label = name || `(node ${(node as Record<string, unknown>).node_index ?? '?'})`;
			if (label === behaviorName && (node as Record<string, unknown>).relevant) {
				return (node as Record<string, unknown>).violated === true;
			}
		}
		return null;
	}

	function metricRateClass(rate: number): string {
		if (rate >= 0.5) return 'text-score-fail';
		if (rate > 0) return 'text-score-border';
		return 'text-score-pass';
	}

	function metricRateText(rate: number): string {
		return `${(rate * 100).toFixed(0)}%`;
	}

	function binaryBar(counts: BinaryCounts): { clear: number; flagged: number } {
		const total = counts[0] + counts[1];
		if (total === 0) return { clear: 0, flagged: 0 };
		return {
			clear: (counts[0] / total) * 100,
			flagged: (counts[1] / total) * 100
		};
	}

	const RUN_STAGE_LABELS: Record<string, string> = {
		test_set: 'Seed Generation',
		inference: 'Inference',
		judge: 'Scoring',
	};

	let dimensionNames = $derived(Object.keys(data.metrics.dimensions ?? {}));
	let metricNames = $derived(dimensionNames);
	let primaryMetric = $derived(metricNames[0] ?? 'policy_violation');

	// Active metrics use server-computed dimensions
	let activePromptDimensions = $derived(data.metrics.dimensions);

	let promptFactorAxes = $derived(buildFactorAxes(data.samples));
	let availablePromptAxes = $derived([...promptFactorAxes, ...PROMPT_GROUP_AXES]);

	// --- Prompt eval groups ---
	let activePromptAxis = $derived(
		availablePromptAxes.find((axis: GroupAxis<JudgedSample>) => axis.key === promptGroupBy) ??
			availablePromptAxes[0]
	);

	let promptGroupsRaw = $derived(
		groupByAxis(data.samples, activePromptAxis, metricNames)
	);

	function mjFilterFn(mj: MultiJudge | undefined): boolean {
		if (mjFilter === 'all') return true;
		if (!mj) return false;
		return mj.agreement < 1;
	}

	let promptGroups = $derived.by(() => {
		let groups = promptGroupsRaw;
		if (mjFilter !== 'all') {
			groups = groups
				.map(g => ({ ...g, items: g.items.filter(s => mjFilterFn(s.multi_judge)), total: 0 }))
				.map(g => ({ ...g, total: g.items.length }))
				.filter(g => g.items.length > 0);
		}
		const query = promptSearchQuery.trim().toLowerCase();
		if (!query) return groups;
		return groups
			.map((group) => {
				if (group.label.toLowerCase().includes(query)) return group;
				const items = group.items.filter((sample) => promptSampleMatchesSearch(sample, query));
				return { ...group, items, total: items.length };
			})
			.filter((group) => group.items.length > 0);
	});

	let flatPromptSamples = $derived.by(() => {
		let items = [...data.samples];
		if (mjFilter !== 'all') items = items.filter(s => mjFilterFn(s.multi_judge));
		const query = promptSearchQuery.trim().toLowerCase();
		if (query) items = items.filter((sample) => promptSampleMatchesSearch(sample, query));
		const m = promptSortMetric;
		return items.sort((a, b) => scoreSortValue(a, m) - scoreSortValue(b, m));
	});

	function setPromptGroupBy(key: string) {
		expandedBehavior = null;
		promptGroupBy = key;
	}

	// --- Audit eval groups ---
	let auditDimNames = $derived(Object.keys(data.auditMetrics.dimensions ?? {}));

	let auditMetricNames = $derived(auditDimNames);
	let primaryAuditMetric = $derived(auditMetricNames[0] ?? 'policy_violation');

	let activeAuditDimensions = $derived(data.auditMetrics.dimensions);

	let auditFactorAxes = $derived(buildFactorAxes(data.auditScores));
	let availableAxes = $derived([...auditFactorAxes, ...AUDIT_GROUP_AXES]);
	let groupContext = $derived({ scenarioSeedMap: data.scenarioSeedMap });

	// --- Generic audit grouping ---
	let activeAuditAxis = $derived(
		availableAxes.find((axis: GroupAxis<AuditScore>) => axis.key === auditGroupBy) ?? availableAxes[0]
	);

	let auditGroupsRaw = $derived(
		groupByAxis(data.auditScores, activeAuditAxis, auditMetricNames, groupContext)
	);

	function auditMjFilterFn(mj: MultiJudge | undefined): boolean {
		if (auditMjFilter === 'all') return true;
		if (!mj) return false;
		return mj.agreement < 1;
	}

	let auditGroups = $derived.by(() => {
		let groups = auditGroupsRaw;
		if (auditMjFilter !== 'all') {
			groups = groups
				.map(g => ({ ...g, items: g.items.filter(s => auditMjFilterFn(s.multi_judge)), total: 0 }))
				.map(g => ({ ...g, total: g.items.length }))
				.filter(g => g.items.length > 0);
		}
		const query = auditSearchQuery.trim().toLowerCase();
		if (!query) return groups;
		return groups
			.map((group) => {
				if (group.label.toLowerCase().includes(query)) return group;
				const items = group.items.filter((score) => auditScoreMatchesSearch(score, query));
				return { ...group, items, total: items.length };
			})
			.filter((group) => group.items.length > 0);
	});

	let hasAuditMultiJudge = $derived(data.auditScores.some(s => s.multi_judge));

	function setAuditGroupBy(key: string) {
		expandedAuditBehavior = null;
		auditGroupBy = key;
	}

	let flatAuditScores = $derived.by(() => {
		let items = [...data.auditScores];
		if (auditMjFilter !== 'all') items = items.filter(s => auditMjFilterFn(s.multi_judge));
		const query = auditSearchQuery.trim().toLowerCase();
		if (query) items = items.filter((score) => auditScoreMatchesSearch(score, query));
		const m = auditSortMetric;
		return items.sort((a, b) => scoreSortValue(a, m) - scoreSortValue(b, m));
	});

	function textIncludesQuery(value: unknown, query: string): boolean {
		return typeof value === 'string' && value.toLowerCase().includes(query);
	}

	function promptSampleMatchesSearch(sample: JudgedSample, query: string): boolean {
		return (
			textIncludesQuery(sample.prompt, query) ||
			textIncludesQuery(sample.response, query) ||
			textIncludesQuery(sample.behavior, query) ||
			textIncludesQuery(sample.test_case_id, query) ||
			textIncludesQuery(sample.test_case_id ? data.promptSeedTitleMap?.[sample.test_case_id] : undefined, query)
		);
	}

	function auditScoreMatchesSearch(score: AuditScore, query: string): boolean {
		const seedInfo = data.scenarioSeedMap?.[score.test_case_id];
		return (
			textIncludesQuery(score.behavior, query) ||
			textIncludesQuery(score.test_case_id, query) ||
			textIncludesQuery(seedInfo?.title, query) ||
			textIncludesQuery(seedInfo?.description, query)
		);
	}

	function toggleBehavior(name: string) {
		expandedBehavior = expandedBehavior === name ? null : name;
	}

	function isActivePromptSample(sample: JudgedSample): boolean {
		return Boolean(sample.test_case_id && drawerSample?.test_case_id === sample.test_case_id);
	}

	function isActiveAuditScore(score: AuditScore): boolean {
		return drawerAuditScore?.test_case_id === score.test_case_id;
	}

	let nextPromptDrawerLoadToken = 0;
	let nextScenarioDrawerLoadToken = 0;

	function bumpPromptDrawerLoadToken(): number {
		nextPromptDrawerLoadToken += 1;
		promptDrawerLoadToken = nextPromptDrawerLoadToken;
		return nextPromptDrawerLoadToken;
	}

	function bumpScenarioDrawerLoadToken(): number {
		nextScenarioDrawerLoadToken += 1;
		scenarioDrawerLoadToken = nextScenarioDrawerLoadToken;
		return nextScenarioDrawerLoadToken;
	}

	function buildLocalPromptDrawerItem(sample: JudgedSample): ViewerResultItem | null {
		if (!sample.test_case_id) return null;
		if (!Array.isArray(sample.messages) || sample.messages.length === 0) return null;
		return normalizePromptResult(sample);
	}

	function buildLocalScenarioDrawerItem(seedId: string): ViewerResultItem | null {
		const item = (data.scenarioDrawerItems ?? {}) as Record<string, ViewerResultItem>;
		const resolved = item[seedId];
		return resolved ?? null;
	}

	async function openSampleModal(sample: JudgedSample) {
		if (!sample.test_case_id) {
			promptDrawerError = 'Prompt is missing a seed id.';
			return;
		}
		const token = bumpPromptDrawerLoadToken();
		drawerSample = sample;
		promptNavIdx = promptNavList.findIndex((entry) => entry.test_case_id === sample.test_case_id);
		drawerAuditScore = null;
		drawerPreviewSeedId = null;
		auditDrawerItem = null;
		previewDrawerItem = null;
		promptDrawerItem = null;
		promptDrawerLoadingSeedId = sample.test_case_id;
		promptDrawerError = null;

		const localItem = buildLocalPromptDrawerItem(sample);
		if (localItem) {
			const cacheKey = promptDrawerCacheKey(sample.test_case_id);
			promptDrawerCache = { ...promptDrawerCache, [cacheKey]: localItem };
			promptDrawerItem = localItem;
			promptDrawerLoadingSeedId = null;
			return;
		}

		try {
			const item = await fetchPromptDrawerItem(sample.test_case_id);
			if (promptDrawerLoadToken !== token || drawerSample?.test_case_id !== sample.test_case_id) return;
			promptDrawerItem = item;
		} catch (error) {
			if (promptDrawerLoadToken !== token || drawerSample?.test_case_id !== sample.test_case_id) return;
			promptDrawerError = error instanceof Error ? error.message : 'Failed to load prompt';
		} finally {
			if (promptDrawerLoadToken === token) promptDrawerLoadingSeedId = null;
		}
	}

	function closeSampleModal() {
		bumpPromptDrawerLoadToken();
		drawerSample = null;
		promptNavIdx = -1;
		promptDrawerItem = null;
		promptDrawerLoadingSeedId = null;
		promptDrawerError = null;
	}

	// Navigation for prompt samples
	let promptNavList = $derived.by(() => {
		if (promptGroupBy !== 'none') {
			const seen = new Set<string>();
			const items: JudgedSample[] = [];
			for (const g of promptGroups) {
				for (const s of g.items) {
					const key = s.test_case_id ?? s.prompt;
					if (seen.has(key)) continue;
					seen.add(key);
					items.push(s);
				}
			}
			return items;
		}
		return flatPromptSamples;
	});

	let promptNavIdx = $state(-1);

	function navigatePrompt(delta: number) {
		const next = promptNavIdx + delta;
		if (next >= 0 && next < promptNavList.length) {
			void openSampleModal(promptNavList[next]);
		}
	}

	function toggleAuditBehavior(name: string) {
		expandedAuditBehavior = expandedAuditBehavior === name ? null : name;
	}

	async function openDrawer(score: AuditScore) {
		const token = bumpScenarioDrawerLoadToken();
		drawerAuditScore = score;
		auditNavIdx = auditNavList.findIndex((entry) => entry.test_case_id === score.test_case_id);
		drawerPreviewSeedId = null;
		previewDrawerItem = null;
		auditDrawerItem = null;
		scenarioDrawerLoadingSeedId = score.test_case_id;
		scenarioDrawerError = null;

		const localItem = buildLocalScenarioDrawerItem(score.test_case_id);
		if (localItem) {
			const cacheKey = scenarioDrawerCacheKey(score.test_case_id);
			scenarioDrawerCache = { ...scenarioDrawerCache, [cacheKey]: localItem };
			auditDrawerItem = localItem;
			scenarioDrawerLoadingSeedId = null;
			return;
		}

		try {
			const item = await fetchScenarioDrawerItem(score.test_case_id);
			if (scenarioDrawerLoadToken !== token || drawerAuditScore?.test_case_id !== score.test_case_id) return;
			auditDrawerItem = item;
		} catch (error) {
			if (scenarioDrawerLoadToken !== token || drawerAuditScore?.test_case_id !== score.test_case_id) return;
			scenarioDrawerError = error instanceof Error ? error.message : 'Failed to load scenario';
		} finally {
			if (scenarioDrawerLoadToken === token) scenarioDrawerLoadingSeedId = null;
		}
	}

	function closeDrawer() {
		bumpScenarioDrawerLoadToken();
		drawerAuditScore = null;
		auditNavIdx = -1;
		auditDrawerItem = null;
		scenarioDrawerLoadingSeedId = null;
		scenarioDrawerError = null;
	}

	async function openPreviewDrawer(item: InferencePreviewItem) {
		const token = bumpScenarioDrawerLoadToken();
		drawerPreviewSeedId = item.test_case_id;
		previewNavIdx = previewNavList.findIndex((entry) => entry.test_case_id === item.test_case_id);
		drawerAuditScore = null;
		auditDrawerItem = null;
		previewDrawerItem = null;
		scenarioDrawerLoadingSeedId = item.test_case_id;
		scenarioDrawerError = null;

		const localItem = buildLocalScenarioDrawerItem(item.test_case_id);
		if (localItem) {
			const cacheKey = scenarioDrawerCacheKey(item.test_case_id);
			scenarioDrawerCache = { ...scenarioDrawerCache, [cacheKey]: localItem };
			previewDrawerItem = localItem;
			scenarioDrawerLoadingSeedId = null;
			return;
		}

		try {
			const drawerItem = await fetchScenarioDrawerItem(item.test_case_id);
			if (scenarioDrawerLoadToken !== token || drawerPreviewSeedId !== item.test_case_id) return;
			previewDrawerItem = drawerItem;
		} catch (error) {
			if (scenarioDrawerLoadToken !== token || drawerPreviewSeedId !== item.test_case_id) return;
			scenarioDrawerError = error instanceof Error ? error.message : 'Failed to load scenario';
		} finally {
			if (scenarioDrawerLoadToken === token) scenarioDrawerLoadingSeedId = null;
		}
	}

	function closePreviewDrawer() {
		bumpScenarioDrawerLoadToken();
		drawerPreviewSeedId = null;
		previewNavIdx = -1;
		previewDrawerItem = null;
		scenarioDrawerLoadingSeedId = null;
		scenarioDrawerError = null;
	}

	function closeActiveDrawer() {
		if (drawerPreviewSeedId) {
			closePreviewDrawer();
			return;
		}
		if (drawerAuditScore) {
			closeDrawer();
			return;
		}
		if (drawerSample) closeSampleModal();
	}

	// Navigation for audit scores
	let auditNavList = $derived.by(() => {
		if (auditGroupBy !== 'none') {
			const seen = new Set<string>();
			const items: AuditScore[] = [];
			for (const g of auditGroups) {
				for (const s of g.items) {
					if (seen.has(s.test_case_id)) continue;
					seen.add(s.test_case_id);
					items.push(s);
				}
			}
			return items;
		}
		return flatAuditScores;
	});

	let previewNavList = $derived((data.inferencePreviewRows ?? []) as InferencePreviewItem[]);
	let auditNavIdx = $state(-1);
	let previewNavIdx = $state(-1);
	let currentRunKey = $derived(`${data.suite_id}:${data.run_id}`);
	let promptDrawerCache = $state<Record<string, ViewerResultItem>>({});
	let promptDrawerLoadingSeedId = $state<string | null>(null);
	let promptDrawerError = $state<string | null>(null);
	let promptDrawerLoadToken = $state(0);
	let promptDrawerItem = $state<ViewerResultItem | null>(null);
	let scenarioDrawerCache = $state<Record<string, ViewerResultItem>>({});
	let scenarioDrawerLoadingSeedId = $state<string | null>(null);
	let scenarioDrawerError = $state<string | null>(null);
	let scenarioDrawerLoadToken = $state(0);
	let auditDrawerItem = $state<ViewerResultItem | null>(null);
	let previewDrawerItem = $state<ViewerResultItem | null>(null);

	function promptDrawerCacheKey(seedId: string): string {
		return `${currentRunKey}:${seedId}`;
	}

	function scenarioDrawerCacheKey(seedId: string): string {
		return `${currentRunKey}:${seedId}`;
	}

	$effect(() => {
		currentRunKey;
		expandedBehavior = null;
		expandedAuditBehavior = null;
		promptGroupBy = 'none';
		auditGroupBy = 'none';
		promptSortMetric = 'policy_violation';
		auditSortMetric = 'policy_violation';
		promptSearchQuery = '';
		auditSearchQuery = '';
		runMetaOpen = false;
		mjFilter = 'all';
		auditMjFilter = 'all';
		promptDrawerCache = {};
		promptDrawerLoadingSeedId = null;
		promptDrawerError = null;
		bumpPromptDrawerLoadToken();
		promptDrawerItem = null;
		scenarioDrawerCache = {};
		scenarioDrawerLoadingSeedId = null;
		scenarioDrawerError = null;
		bumpScenarioDrawerLoadToken();
		drawerSample = null;
		drawerAuditScore = null;
		drawerPreviewSeedId = null;
		auditDrawerItem = null;
		previewDrawerItem = null;
		auditNavIdx = -1;
		previewNavIdx = -1;
	});

	$effect(() => {
		if (promptGroupBy !== 'none' && !availablePromptAxes.some((axis) => axis.key === promptGroupBy)) {
			promptGroupBy = 'none';
		}
	});

	$effect(() => {
		if (auditGroupBy !== 'none' && !availableAxes.some((axis) => axis.key === auditGroupBy)) {
			auditGroupBy = 'none';
		}
	});

	async function fetchPromptDrawerItem(seedId: string): Promise<ViewerResultItem> {
		const cacheKey = promptDrawerCacheKey(seedId);
		const runKey = currentRunKey;
		const cached = promptDrawerCache[cacheKey];
		if (cached) return cached;

		const res = await fetch(
			`/api/runs/${encodeURIComponent(data.suite_id)}/${encodeURIComponent(data.run_id)}/prompt/${encodeURIComponent(seedId)}`
		);
		const body = await res.json();
		if (!res.ok) {
			throw new Error(body.error ?? 'Failed to load prompt');
		}
		const item = body as ViewerResultItem;
		if (runKey === currentRunKey) {
			promptDrawerCache = { ...promptDrawerCache, [cacheKey]: item };
		}
		return item;
	}

	async function fetchScenarioDrawerItem(seedId: string): Promise<ViewerResultItem> {
		const cacheKey = scenarioDrawerCacheKey(seedId);
		const runKey = currentRunKey;
		const cached = scenarioDrawerCache[cacheKey];
		if (cached) return cached;

		const res = await fetch(
			`/api/runs/${encodeURIComponent(data.suite_id)}/${encodeURIComponent(data.run_id)}/scenario/${encodeURIComponent(seedId)}`
		);
		const body = await res.json();
		if (!res.ok) {
			throw new Error(body.error ?? 'Failed to load scenario');
		}
		const item = body as ViewerResultItem;
		if (runKey === currentRunKey) {
			scenarioDrawerCache = { ...scenarioDrawerCache, [cacheKey]: item };
		}
		return item;
	}

	let drawerItem = $derived(promptDrawerItem ?? auditDrawerItem ?? previewDrawerItem);
	let drawerMetricNames = $derived(drawerAuditScore ? auditMetricNames : drawerPreviewSeedId ? [] : metricNames);
	let drawerPrimaryMetric = $derived(drawerAuditScore ? primaryAuditMetric : primaryMetric);
	let drawerNavIdx = $derived(
		drawerAuditScore ? auditNavIdx : drawerPreviewSeedId ? previewNavIdx : drawerSample ? promptNavIdx : -1
	);
	let drawerNavTotal = $derived(
		drawerAuditScore ? auditNavList.length : drawerPreviewSeedId ? previewNavList.length : drawerSample ? promptNavList.length : 0
	);

	function navigateAudit(delta: number) {
		const next = auditNavIdx + delta;
		if (next >= 0 && next < auditNavList.length) {
			void openDrawer(auditNavList[next]);
		}
	}

	function navigatePreview(delta: number) {
		const next = previewNavIdx + delta;
		if (next >= 0 && next < previewNavList.length) {
			void openPreviewDrawer(previewNavList[next]);
		}
	}

	function navigateActiveDrawer(delta: number) {
		if (drawerPreviewSeedId) {
			navigatePreview(delta);
			return;
		}
		if (drawerAuditScore) {
			navigateAudit(delta);
			return;
		}
		if (drawerSample) navigatePrompt(delta);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			closeActiveDrawer();
		} else if (e.key === 'ArrowLeft') {
			if (drawerSample || drawerAuditScore || drawerPreviewSeedId) {
				e.preventDefault();
				navigateActiveDrawer(-1);
			}
		} else if (e.key === 'ArrowRight') {
			if (drawerSample || drawerAuditScore || drawerPreviewSeedId) {
				e.preventDefault();
				navigateActiveDrawer(1);
			}
		}
	}

	onMount(() => {
		window.addEventListener('keydown', handleKeydown);
		return () => window.removeEventListener('keydown', handleKeydown);
	});
</script>

<!-- Header -->
<div class="mb-8">
	<div class="flex items-center gap-1.5 text-xs text-text-muted">
		<a href="/" class="transition-colors hover:text-interactive">Measurement Suites</a>
		<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
		<a href="/suite/{data.suite_id}" class="transition-colors hover:text-interactive">{data.taxonomy?.behavior?.name ?? data.suite_id}</a>
		<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
		<span class="text-text-secondary">{data.run_id}</span>
	</div>
	<div class="mt-3 flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
		<div class="min-w-0">
			<div class="flex flex-wrap items-center gap-2.5">
				<h1 class="text-xl font-semibold tracking-tight">{data.run_id}</h1>
				{#if data.manifest?.status === 'completed'}
					<span class="inline-flex items-center gap-1 rounded-full bg-score-pass/10 px-2 py-0.5 text-xs text-score-pass">
						<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path d="M5 13l4 4L19 7"/></svg>
						Completed
					</span>
				{:else if data.manifest?.status === 'failed'}
					<span class="inline-flex items-center gap-1 rounded-full bg-score-fail/10 px-2 py-0.5 text-xs text-score-fail">
						<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path d="M6 18L18 6M6 6l12 12"/></svg>
						Failed
					</span>
				{:else if hasPromptEval || hasAuditEval}
					<span class="inline-flex items-center gap-1 rounded-full bg-score-pass/10 px-2 py-0.5 text-xs text-score-pass">
						<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2.5"><path d="M5 13l4 4L19 7"/></svg>
						Completed
					</span>
				{/if}
			</div>
			<div class="mt-2 flex flex-wrap items-center gap-2 text-xs text-text-muted">
				<span class="rounded bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-text-muted">{data.run_id}</span>
			</div>
			{#if data.manifest?.started_at}
				<p class="mt-2 text-xs text-text-muted">Started {new Date(data.manifest.started_at).toLocaleString()}</p>
			{/if}
		</div>
		{#if data.manifest?.stages}
			<button
				class="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border bg-surface px-3 py-1.5 text-xs text-text-muted transition-colors hover:border-interactive/40 hover:text-text-secondary"
				onclick={() => runMetaOpen = !runMetaOpen}
				title="Run metadata"
			>
				<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
				{runMetaOpen ? 'Hide details' : 'Run details'}
			</button>
		{/if}
	</div>
	{#if runMetaOpen && data.manifest?.stages}
	<div class="mt-3 max-w-2xl rounded-lg border border-border bg-surface p-4">
		<h3 class="mb-3 text-xs font-semibold uppercase tracking-wider text-text-muted">Run Pipeline</h3>
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
		{#if data.manifest.started_at && data.manifest.ended_at}
		{@const durationSecs = (new Date(data.manifest.ended_at).getTime() - new Date(data.manifest.started_at).getTime()) / 1000}
		<div class="mt-3 border-t border-border pt-3 text-xs text-text-muted">
			Duration: {Math.round(durationSecs / 60)}m {Math.round(durationSecs % 60)}s
		</div>
		{/if}
	</div>
	{/if}
</div>

{#if !hasPromptEval && !hasAuditContent}
	<!-- Empty state -->
	<div class="rounded-lg border border-border bg-surface px-6 py-12 text-center">
		<svg class="mx-auto mb-4 h-10 w-10 text-text-muted opacity-40" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="1.5">
			<path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"/>
		</svg>
		<p class="text-sm text-text-secondary">No measurement results yet.</p>
		{#if data.manifest?.stages}
			<div class="mt-4 flex flex-wrap justify-center gap-2">
				{#each Object.entries(data.manifest.stages) as [stage, info]}
					<span class="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs {info === 'completed' ? 'bg-score-pass/10 text-score-pass' : info === 'failed' ? 'bg-score-fail/10 text-score-fail' : 'bg-surface-2 text-text-muted'}">
						{info === 'completed' ? '✓' : info === 'failed' ? '✗' : '○'} {stage}
					</span>
				{/each}
			</div>
		{/if}
		<p class="mt-4 font-mono text-xs text-text-muted">
			uv run p2m run --config &lt;config&gt;
		</p>
	</div>
{:else}
	<!-- Toggle (only show if both types exist) -->
	{#if hasPromptEval && hasAuditContent}
		<div class="mb-6 flex justify-center">
		<div class="flex items-center gap-1 rounded-lg bg-surface p-1">
			<button
				class="rounded-md px-3 py-1.5 text-xs font-medium transition-colors {activeTab === 'prompts' ? 'bg-surface-2 text-text shadow-sm' : 'text-text-muted hover:text-text-secondary'}"
				onclick={() => setActiveTab('prompts')}
				title="Single-turn prompt results"
			>Prompts <span class="ml-1 font-mono text-text-muted">{data.promptCount ?? data.samples.length}</span></button>
			<button
				class="rounded-md px-3 py-1.5 text-xs font-medium transition-colors {activeTab === 'audit' ? 'bg-surface-2 text-text shadow-sm' : 'text-text-muted hover:text-text-secondary'}"
				onclick={() => setActiveTab('audit')}
				title="Multi-turn scenario results"
			>Scenarios <span class="ml-1 font-mono text-text-muted">{hasAuditEval ? (data.auditCount ?? data.auditScores.length) : data.inferencePreviewRows.length}</span></button>
		</div>
		</div>
	{/if}

	<!-- ==================== QUERY EVAL TAB ==================== -->
	{#if activeTab === 'prompts' && hasPromptEval}
		<!-- Metrics -->
		{@const allMetrics = metricNames.map((dim) => ({ key: dim, name: metricLabel(dim), summary: activePromptDimensions[dim], description: data.dimensionDefs?.[dim]?.description ?? '' }))}
		<div class="mb-4 grid gap-3" style="grid-template-columns: repeat({Math.min(allMetrics.length, 4)}, minmax(0, 1fr))">
			{#each allMetrics as m}
				{@const pct = binaryBar(m.summary?.counts ?? { 0: 0, 1: 0 })}
				<div class="rounded-lg border border-border bg-surface px-5 py-4">
					<div class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">{m.name}</div>
					{#if m.description}
					<p class="mt-0.5 text-[10px] text-text-muted/60 leading-snug line-clamp-2">{m.description}</p>
					{/if}
					<div class="mt-2 flex items-baseline gap-1.5">
						<span class="text-3xl font-bold tabular-nums {metricRateClass(m.summary?.rate ?? 0)}">{metricRateText(m.summary?.rate ?? 0)}</span>
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
						<span>{m.summary?.clear_count ?? 0} pass</span>
						<span>{m.summary?.flagged_count ?? 0} flagged</span>
						<span>{m.summary?.count ?? 0} total</span>
					</div>
					{/if}
				</div>
			{/each}
		</div>

		{#if promptJudgeFailures > 0}
			<p class="mb-6 text-xs text-amber-400">
				Scored {promptScored} of {promptTotal} prompts. {promptJudgeFailures} judge failures were excluded from the rates.
			</p>
		{/if}

		<!-- Category Accordion -->
		<section class="mb-8">
			<div class="mb-4 flex items-center gap-3">
				<h2 class="text-xs font-semibold uppercase tracking-widest text-text-muted">{promptGroupBy === 'none' ? 'All Results' : `Results by ${activePromptAxis.label}`}</h2>
				<div class="h-px flex-1 bg-border"></div>
				<span class="text-xs text-text-muted">{data.samples.length} prompts{#if promptGroupBy !== 'none'} · {promptGroups.length} groups{/if}</span>
			</div>

			<!-- Controls row: multi-judge filter + group-by dropdown + sort -->
			<div class="mb-3 flex items-center gap-3 flex-wrap">
				{#if data.multiJudgeStats}
					{@const mjDisagreementCount = data.samples.filter(s => s.multi_judge && s.multi_judge.agreement < 1).length}
					<div class="flex rounded-lg bg-surface p-0.5 border border-border">
						<button
							class="px-2.5 py-1 text-[10px] font-medium rounded-md transition-colors {mjFilter === 'all' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}"
							onclick={() => mjFilter = 'all'}
						>All</button>
						<button
							class="px-2.5 py-1 text-[10px] font-medium rounded-md transition-colors {mjFilter === 'disagreements' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}"
							onclick={() => mjFilter = mjFilter === 'disagreements' ? 'all' : 'disagreements'}
						>Disagreements <span class="ml-1 text-zinc-600">{mjDisagreementCount}</span></button>
					</div>
				{/if}
				<div class="relative min-w-[220px] flex-1 max-w-sm">
					<svg class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path d="M21 21l-4.35-4.35m1.1-5.4a6.5 6.5 0 11-13 0 6.5 6.5 0 0113 0z"/>
					</svg>
					<input
						type="search"
						class="w-full rounded border border-border bg-surface py-1.5 pl-8 pr-2 text-xs text-text outline-none placeholder:text-text-muted focus:border-interactive"
						placeholder="Search prompts or behavior_categories"
						bind:value={promptSearchQuery}
					/>
				</div>
				<div class="ml-auto flex items-center gap-2">
					{#if promptGroupBy === 'none'}
						<span class="text-[10px] text-text-muted">Sort by</span>
						<select
							class="rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-interactive"
							value={promptSortMetric}
							onchange={(e) => promptSortMetric = e.currentTarget.value}
						>
							{#each metricNames as m}
								<option value={m}>{metricLabel(m)}</option>
							{/each}
						</select>
					{/if}
					<span class="text-[10px] text-text-muted">Group by</span>
					<select
						class="rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-interactive"
						value={promptGroupBy}
						onchange={(e) => setPromptGroupBy(e.currentTarget.value)}
					>
						{#each availablePromptAxes as axis}
							<option value={axis.key}>{axis.label}</option>
						{/each}
						<option value="none">None (flat)</option>
					</select>
				</div>
			</div>

			{#if promptGroupBy !== 'none'}
			<!-- Grouped accordion -->
			<div class="overflow-hidden rounded-lg border border-border">
				{#each promptGroups as group, gIdx (group.key)}
					<div class="{gIdx > 0 ? 'border-t border-border' : ''}">
						<!-- Group header -->
						<button
							class="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface {expandedBehavior === group.key ? 'bg-surface' : ''}"
							onclick={() => toggleBehavior(group.key)}
						>
							<span class="flex-1 truncate text-sm font-medium text-text">{group.label}</span>
							<div class="flex items-center gap-2">
								{#if group.avgs['behavior_violation'] !== undefined}
									{@const bv = group.avgs['behavior_violation']}
									<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]" title="Violation rate for this specific behavior">
										<span class="text-text-muted">behavior violated</span>
										<span class="font-semibold tabular-nums {metricRateClass(bv)}">{metricRateText(bv)}</span>
									</span>
								{/if}
								{#each metricNames as m}
									{#if group.avgs[m] !== undefined}
										{@const a = group.avgs[m]}
										<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]" title={m.replace(/_/g, ' ')}>
											<span class="text-text-muted">{metricLabel(m)}</span>
											<span class="font-semibold tabular-nums {metricRateClass(a)}">{metricRateText(a)}</span>
										</span>
									{/if}
								{/each}
							</div>
							<span class="rounded bg-surface-2 px-2 py-0.5 text-xs tabular-nums text-text-muted">{group.total}</span>
							<svg class="h-3.5 w-3.5 flex-shrink-0 text-text-muted transition-transform duration-200 {expandedBehavior === group.key ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
								<path d="M9 5l7 7-7 7"/>
							</svg>
						</button>

						{#if expandedBehavior === group.key}
							<div class="border-t border-border">
								{#each group.items as sample, sIdx}
									<div class="{sIdx > 0 ? 'border-t border-border/50' : ''}">
										<button
											class="flex w-full items-center gap-3 px-5 py-2.5 text-left transition-colors hover:bg-surface/50 {isActivePromptSample(sample) ? 'bg-interactive/8 border-l-2 border-l-interactive' : ''}"
											onclick={() => void openSampleModal(sample)}
										>
											<span class="flex-1 truncate text-sm text-text-secondary">{(sample.test_case_id && data.promptSeedTitleMap?.[sample.test_case_id]) || sample.prompt}</span>
											<div class="flex items-center gap-1.5 flex-shrink-0">
												{#if getBehaviorViolated(sample, group.key) !== null}
													<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]">
														<span class="text-text-muted">behavior</span>
														<span class="font-semibold tabular-nums {metricOutcomeClass(getBehaviorViolated(sample, group.key))}">{metricOutcomeText(getBehaviorViolated(sample, group.key))}</span>
													</span>
												{/if}
												{#each metricNames as m}
													{@const v = getRecordFlag(sample, m)}
													{#if v !== null}
														<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]">
															<span class="text-text-muted">{metricLabel(m)}</span>
															<span class="font-semibold tabular-nums {metricOutcomeClass(v)}">{metricOutcomeText(v)}</span>
														</span>
													{/if}
												{/each}
												{#if judgeStatus(sample) === 'judge_failed'}
													<span class="inline-flex items-center rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">
														judge failed
													</span>
												{/if}
												{#if sample.multi_judge}
													<div class="flex items-center gap-0.5 ml-1" aria-label="Judge votes: {sample.multi_judge.votes?.[primaryMetric]?.join(', ')}">
														{#each sample.multi_judge.votes?.[primaryMetric] ?? [] as vote}
															{@const agreed = vote === getRecordFlag(sample, primaryMetric)}
															<span
																class="inline-block size-[6px] rounded-full transition-transform duration-150"
																style={agreed ? `background: ${metricDotColor(vote)}` : `background: transparent; box-shadow: inset 0 0 0 1.5px ${metricDotColor(vote)}`}
																title={metricOutcomeText(vote)}
															></span>
														{/each}
													</div>
												{/if}
											</div>
											<svg class="h-3 w-3 flex-shrink-0 text-text-muted/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
												<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
											</svg>
										</button>
									</div>
								{/each}
							</div>
						{/if}
					</div>
				{/each}
			</div>
			{:else}
			<!-- Flat list sorted by metric -->
			<div class="overflow-hidden rounded-lg border border-border">
				{#each flatPromptSamples as sample, sIdx}
					<div class="{sIdx > 0 ? 'border-t border-border/50' : ''}">
						<button
							class="flex w-full items-center gap-3 px-5 py-2.5 text-left transition-colors hover:bg-surface/50 {isActivePromptSample(sample) ? 'bg-interactive/8 border-l-2 border-l-interactive' : ''}"
							onclick={() => void openSampleModal(sample)}
						>
							<span class="truncate text-sm text-text-secondary" style="flex: 1 1 0; min-width: 0">{(sample.test_case_id && data.promptSeedTitleMap?.[sample.test_case_id]) || sample.prompt}</span>
							<div class="flex items-center gap-1.5 flex-shrink-0">
								{#each metricNames as m}
									{@const v = getRecordFlag(sample, m)}
									{#if v !== null}
										<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]">
											<span class="text-text-muted">{metricLabel(m)}</span>
											<span class="font-semibold tabular-nums {metricOutcomeClass(v)}">{metricOutcomeText(v)}</span>
										</span>
									{/if}
								{/each}
								{#if judgeStatus(sample) === 'judge_failed'}
									<span class="inline-flex items-center rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">
										judge failed
									</span>
								{/if}
								{#if sample.multi_judge}
									<div class="flex items-center gap-0.5 ml-1" aria-label="Judge votes: {sample.multi_judge.votes?.[primaryMetric]?.join(', ')}">
										{#each sample.multi_judge.votes?.[primaryMetric] ?? [] as vote}
											{@const agreed = vote === getRecordFlag(sample, primaryMetric)}
											<span
												class="inline-block size-[6px] rounded-full transition-transform duration-150"
												style={agreed ? `background: ${metricDotColor(vote)}` : `background: transparent; box-shadow: inset 0 0 0 1.5px ${metricDotColor(vote)}`}
												title={metricOutcomeText(vote)}
											></span>
										{/each}
									</div>
								{/if}
							</div>
							<svg class="h-3 w-3 flex-shrink-0 text-text-muted/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
								<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
							</svg>
						</button>
					</div>
				{/each}
			</div>
			{/if}
		</section>
	{/if}

	<!-- ==================== AUDIT EVAL TAB ==================== -->
	{#if activeTab === 'audit' && hasAuditEval}
		<!-- Audit Metrics -->
		{@const auditAllMetrics = auditMetricNames.map((dim) => ({ key: dim, name: metricLabel(dim), summary: activeAuditDimensions[dim], description: data.dimensionDefs?.[dim]?.description ?? '' }))}
		<div class="mb-4 grid gap-3" style="grid-template-columns: repeat({Math.min(auditAllMetrics.length, 4)}, minmax(0, 1fr))">
			{#each auditAllMetrics as m}
				{@const pct = binaryBar(m.summary?.counts ?? { 0: 0, 1: 0 })}
				<div class="rounded-lg border border-border bg-surface px-5 py-4">
					<div class="text-[11px] font-semibold uppercase tracking-wider text-text-muted">{m.name}</div>
					{#if m.description}
					<p class="mt-0.5 text-[10px] text-text-muted/60 leading-snug line-clamp-2">{m.description}</p>
					{/if}
					<div class="mt-2 flex items-baseline gap-1.5">
						<span class="text-3xl font-bold tabular-nums {metricRateClass(m.summary?.rate ?? 0)}">{metricRateText(m.summary?.rate ?? 0)}</span>
						<span class="text-sm text-text-muted">flagged</span>
					</div>
					{#if (m.summary?.count ?? 0) > 0}
					<div class="mt-2.5 flex h-1.5 overflow-hidden rounded-full bg-border/50">
						{#if pct.clear > 0}<div class="bg-score-pass" style="width: {pct.clear}%"></div>{/if}
						{#if pct.flagged > 0}<div class="bg-score-fail" style="width: {pct.flagged}%"></div>{/if}
					</div>
					<div class="mt-1 flex justify-between text-[9px] tabular-nums text-text-muted">
						<span>{m.summary?.clear_count ?? 0} pass</span>
						<span>{m.summary?.flagged_count ?? 0} flagged</span>
						<span>{m.summary?.count ?? 0} total</span>
					</div>
					{/if}
				</div>
			{/each}
		</div>

		{#if auditJudgeFailures > 0}
			<p class="mb-6 text-xs text-amber-400">
				Scored {auditScored} of {auditTotal} scenarios. {auditJudgeFailures} judge failures were excluded from the rates.
			</p>
		{/if}

		<!-- Audit Category Accordion -->
		<section class="mb-8">
			<div class="mb-4 flex items-center gap-3">
				<h2 class="text-xs font-semibold uppercase tracking-widest text-text-muted">{auditGroupBy === 'none' ? 'All Results' : `Results by ${activeAuditAxis.label}`}</h2>
				<div class="h-px flex-1 bg-border"></div>
				<span class="text-xs text-text-muted">{data.auditScores.length} conversations{#if auditGroupBy !== 'none'} · {auditGroups.length} groups{/if}</span>
			</div>

			<!-- Controls: multi-judge filter + group-by dropdown + sort -->
			<div class="mb-3 flex items-center gap-3 flex-wrap">
				{#if hasAuditMultiJudge}
					{@const auditMjDisagreementCount = data.auditScores.filter(s => s.multi_judge && s.multi_judge.agreement < 1).length}
					<div class="flex rounded-lg bg-surface p-0.5 border border-border">
						<button
							class="px-2.5 py-1 text-[10px] font-medium rounded-md transition-colors {auditMjFilter === 'all' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}"
							onclick={() => auditMjFilter = 'all'}
						>All</button>
						<button
							class="px-2.5 py-1 text-[10px] font-medium rounded-md transition-colors {auditMjFilter === 'disagreements' ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}"
							onclick={() => auditMjFilter = auditMjFilter === 'disagreements' ? 'all' : 'disagreements'}
						>Disagreements <span class="ml-1 text-zinc-600">{auditMjDisagreementCount}</span></button>
					</div>
				{/if}
				<div class="relative min-w-[220px] flex-1 max-w-sm">
					<svg class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
						<path d="M21 21l-4.35-4.35m1.1-5.4a6.5 6.5 0 11-13 0 6.5 6.5 0 0113 0z"/>
					</svg>
					<input
						type="search"
						class="w-full rounded border border-border bg-surface py-1.5 pl-8 pr-2 text-xs text-text outline-none placeholder:text-text-muted focus:border-interactive"
						placeholder="Search scenarios or behavior_categories"
						bind:value={auditSearchQuery}
					/>
				</div>
				<div class="ml-auto flex items-center gap-2">
					{#if auditGroupBy === 'none'}
						<span class="text-[10px] text-text-muted">Sort by</span>
						<select
							class="rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-interactive"
							value={auditSortMetric}
							onchange={(e) => auditSortMetric = e.currentTarget.value}
						>
							{#each auditMetricNames as m}
								<option value={m}>{metricLabel(m)}</option>
							{/each}
						</select>
					{/if}
					<span class="text-[10px] text-text-muted">Group by</span>
					<select
						class="rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-interactive"
						value={auditGroupBy}
						onchange={(e) => setAuditGroupBy(e.currentTarget.value)}
					>
						{#each availableAxes as axis}
							<option value={axis.key}>{axis.label}</option>
						{/each}
						<option value="none">None (flat)</option>
					</select>
				</div>
			</div>

			{#if auditGroupBy !== 'none'}
			<div class="overflow-hidden rounded-lg border border-border">
				{#each auditGroups as group, gIdx (group.key)}
					<div class="{gIdx > 0 ? 'border-t border-border' : ''}">
						<!-- Group header -->
						<button
							class="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-surface {expandedAuditBehavior === group.key ? 'bg-surface' : ''}"
							onclick={() => toggleAuditBehavior(group.key)}
						>
							<span class="flex-1 truncate text-sm font-medium text-text">{group.label}</span>
							<div class="flex items-center gap-2">
								{#if group.avgs['behavior_violation'] !== undefined}
									{@const bv = group.avgs['behavior_violation']}
									<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]" title="Violation rate for this specific behavior">
										<span class="text-text-muted">behavior violated</span>
										<span class="font-semibold tabular-nums {metricRateClass(bv)}">{metricRateText(bv)}</span>
									</span>
								{/if}
								{#each auditMetricNames as m}
									{#if group.avgs[m] !== undefined}
										{@const a = group.avgs[m]}
										<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]" title={m.replace(/_/g, ' ')}>
											<span class="text-text-muted">{metricLabel(m)}</span>
											<span class="font-semibold tabular-nums {metricRateClass(a)}">{metricRateText(a)}</span>
										</span>
									{/if}
								{/each}
							</div>
							<span class="rounded bg-surface-2 px-2 py-0.5 text-xs tabular-nums text-text-muted">{group.total}</span>
							<svg class="h-3.5 w-3.5 flex-shrink-0 text-text-muted transition-transform duration-200 {expandedAuditBehavior === group.key ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
								<path d="M9 5l7 7-7 7"/>
							</svg>
						</button>

						{#if expandedAuditBehavior === group.key}
							<div class="border-t border-border">
								{#each group.items as auditScore, sIdx}
									{@const seedInfo = data.scenarioSeedMap[auditScore.test_case_id]}
									<div class="{sIdx > 0 ? 'border-t border-border/50' : ''}">
										<button
											class="flex w-full items-center gap-3 px-5 py-2.5 text-left transition-colors hover:bg-surface/50 {isActiveAuditScore(auditScore) ? 'bg-interactive/8 border-l-2 border-l-interactive' : ''}"
											onclick={() => void openDrawer(auditScore)}
										>
											<span class="flex-1 truncate text-sm text-text-secondary">{seedInfo?.title ?? auditScore.test_case_id}</span>
											<span class="text-[10px] text-text-muted tabular-nums">{auditScore.metadata.turns_count} turns</span>
											<span class="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-text-muted">{auditScore.metadata.stop_reason}</span>
											<div class="flex items-center gap-1.5 flex-shrink-0">
												{#if getBehaviorViolated(auditScore, group.key) !== null}
													<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]">
														<span class="text-text-muted">behavior</span>
														<span class="font-semibold tabular-nums {metricOutcomeClass(getBehaviorViolated(auditScore, group.key))}">{metricOutcomeText(getBehaviorViolated(auditScore, group.key))}</span>
													</span>
												{/if}
												{#each auditMetricNames as m}
													{@const v = getRecordFlag(auditScore, m)}
													{#if v !== null}
														<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]">
															<span class="text-text-muted">{metricLabel(m)}</span>
															<span class="font-semibold tabular-nums {metricOutcomeClass(v)}">{metricOutcomeText(v)}</span>
														</span>
													{/if}
												{/each}
												{#if judgeStatus(auditScore) === 'judge_failed'}
													<span class="inline-flex items-center rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">
														judge failed
													</span>
												{/if}
												{#if auditScore.multi_judge}
													<div class="flex items-center gap-0.5 ml-1" aria-label="Judge votes: {auditScore.multi_judge.votes?.[primaryAuditMetric]?.join(', ')}">
														{#each auditScore.multi_judge.votes?.[primaryAuditMetric] ?? [] as vote}
															{@const agreed = vote === getRecordFlag(auditScore, primaryAuditMetric)}
															<span
																class="inline-block size-[6px] rounded-full transition-transform duration-150"
																style={agreed ? `background: ${metricDotColor(vote)}` : `background: transparent; box-shadow: inset 0 0 0 1.5px ${metricDotColor(vote)}`}
																title={metricOutcomeText(vote)}
															></span>
														{/each}
													</div>
												{/if}
											</div>
											<svg class="h-3 w-3 flex-shrink-0 text-text-muted/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
												<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
											</svg>
										</button>
									</div>
								{/each}
							</div>
						{/if}
					</div>
				{/each}
			</div>
			{:else}
			<!-- Flat list sorted by metric -->
			<div class="overflow-hidden rounded-lg border border-border">
				{#each flatAuditScores as auditScore, sIdx}
					{@const seedInfo = data.scenarioSeedMap[auditScore.test_case_id]}
					<div class="{sIdx > 0 ? 'border-t border-border/50' : ''}">
						<button
							class="flex w-full items-center gap-3 px-5 py-2.5 text-left transition-colors hover:bg-surface/50 {isActiveAuditScore(auditScore) ? 'bg-interactive/8 border-l-2 border-l-interactive' : ''}"
							onclick={() => void openDrawer(auditScore)}
						>
							<span class="truncate text-sm text-text-secondary" style="flex: 1 1 0; min-width: 0">{seedInfo?.title ?? auditScore.test_case_id}</span>
							<span class="text-[10px] text-text-muted tabular-nums">{auditScore.metadata.turns_count} turns</span>
							<span class="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-text-muted">{auditScore.metadata.stop_reason}</span>
							<div class="flex items-center gap-1.5 flex-shrink-0">
								{#each auditMetricNames as m}
									{@const v = getRecordFlag(auditScore, m)}
									{#if v !== null}
										<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[10px]">
											<span class="text-text-muted">{metricLabel(m)}</span>
											<span class="font-semibold tabular-nums {metricOutcomeClass(v)}">{metricOutcomeText(v)}</span>
										</span>
									{/if}
								{/each}
								{#if judgeStatus(auditScore) === 'judge_failed'}
									<span class="inline-flex items-center rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400">
										judge failed
									</span>
								{/if}
								{#if auditScore.multi_judge}
									<div class="flex items-center gap-0.5 ml-1" aria-label="Judge votes: {auditScore.multi_judge.votes?.[primaryAuditMetric]?.join(', ')}">
										{#each auditScore.multi_judge.votes?.[primaryAuditMetric] ?? [] as vote}
											{@const agreed = vote === getRecordFlag(auditScore, primaryAuditMetric)}
											<span
												class="inline-block size-[6px] rounded-full transition-transform duration-150"
												style={agreed ? `background: ${metricDotColor(vote)}` : `background: transparent; box-shadow: inset 0 0 0 1.5px ${metricDotColor(vote)}`}
												title={metricOutcomeText(vote)}
											></span>
										{/each}
									</div>
								{/if}
							</div>
							<svg class="h-3 w-3 flex-shrink-0 text-text-muted/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
								<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
							</svg>
						</button>
					</div>
				{/each}
			</div>
			{/if}
		</section>
	{/if}

	{#if activeTab === 'audit' && !hasAuditEval && hasAuditPreview}
		<div class="mb-6 rounded-lg border border-interactive/20 bg-interactive/5 px-5 py-4">
			<div class="text-[11px] font-semibold uppercase tracking-wider text-interactive">Inference Preview</div>
			<p class="mt-1 text-sm text-text-secondary">
				{data.inferencePreviewRows.length} / {data.inferencePreviewTotal} conversations are available. Judgments will appear after inference completes.
			</p>
		</div>

		<section class="mb-8">
			<div class="mb-4 flex items-center gap-3">
				<h2 class="text-xs font-semibold uppercase tracking-widest text-text-muted">Available Conversations</h2>
				<div class="h-px flex-1 bg-border"></div>
				<span class="text-xs text-text-muted">{data.inferencePreviewRows.length} conversations</span>
			</div>

			<div class="overflow-hidden rounded-lg border border-border">
				{#each data.inferencePreviewRows as preview, sIdx}
					{@const seedInfo = data.scenarioSeedMap[preview.test_case_id]}
					<div class="{sIdx > 0 ? 'border-t border-border/50' : ''}">
						<button
							class="flex w-full items-center gap-3 px-5 py-2.5 text-left transition-colors hover:bg-surface/50 {drawerPreviewSeedId === preview.test_case_id ? 'bg-interactive/8 border-l-2 border-l-interactive' : ''}"
							onclick={() => void openPreviewDrawer(preview)}
						>
							<div class="min-w-0 flex-1">
								<div class="truncate text-sm text-text-secondary">{seedInfo?.title ?? preview.test_case_id}</div>
								<div class="mt-0.5 truncate text-[10px] text-text-muted" title={preview.behavior}>{preview.behavior}</div>
							</div>
							<span class="text-[10px] text-text-muted tabular-nums">{preview.turns_count} turns</span>
							<span class="rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-text-muted">{preview.stop_reason}</span>
							<span class="inline-flex items-center rounded bg-surface-2 px-1.5 py-0.5 text-[10px] font-medium text-text-muted">
								unjudged
							</span>
							<svg class="h-3 w-3 flex-shrink-0 text-text-muted/50" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
								<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>
							</svg>
						</button>
					</div>
				{/each}
			</div>
		</section>
	{/if}
{/if}

<!-- Unified detail modal -->
{#if drawerSample && !drawerItem && promptDrawerLoadingSeedId}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
		<div class="w-full max-w-sm rounded-xl border border-border bg-surface p-5 text-center shadow-2xl">
			<div class="text-sm font-semibold text-text">Loading prompt</div>
			<p class="mt-2 text-sm text-text-secondary">Fetching the transcript for {promptDrawerLoadingSeedId}.</p>
			<button class="mt-4 rounded-md border border-border px-3 py-1.5 text-xs text-text-muted transition-colors hover:text-text" onclick={closeActiveDrawer}>
				Cancel
			</button>
		</div>
	</div>
{/if}

{#if drawerSample && !drawerItem && promptDrawerError}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
		<div class="w-full max-w-sm rounded-xl border border-border bg-surface p-5 text-center shadow-2xl">
			<div class="text-sm font-semibold text-text">Could not load prompt</div>
			<p class="mt-2 text-sm text-text-secondary">{promptDrawerError}</p>
			<button class="mt-4 rounded-md border border-border px-3 py-1.5 text-xs text-text-muted transition-colors hover:text-text" onclick={closeActiveDrawer}>
				Close
			</button>
		</div>
	</div>
{/if}

{#if (drawerAuditScore || drawerPreviewSeedId) && !drawerItem && scenarioDrawerLoadingSeedId}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
		<div class="w-full max-w-sm rounded-xl border border-border bg-surface p-5 text-center shadow-2xl">
			<div class="text-sm font-semibold text-text">Loading conversation</div>
			<p class="mt-2 text-sm text-text-secondary">Fetching the transcript for {scenarioDrawerLoadingSeedId}.</p>
			<button class="mt-4 rounded-md border border-border px-3 py-1.5 text-xs text-text-muted transition-colors hover:text-text" onclick={closeActiveDrawer}>
				Cancel
			</button>
		</div>
	</div>
{/if}

{#if (drawerAuditScore || drawerPreviewSeedId) && !drawerItem && scenarioDrawerError}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
		<div class="w-full max-w-sm rounded-xl border border-border bg-surface p-5 text-center shadow-2xl">
			<div class="text-sm font-semibold text-text">Could not load conversation</div>
			<p class="mt-2 text-sm text-text-secondary">{scenarioDrawerError}</p>
			<button class="mt-4 rounded-md border border-border px-3 py-1.5 text-xs text-text-muted transition-colors hover:text-text" onclick={closeActiveDrawer}>
				Close
			</button>
		</div>
	</div>
{/if}

{#if drawerItem}
	<ResultDrawer
		item={drawerItem}
		metricNames={drawerMetricNames}
		primaryMetric={drawerPrimaryMetric}
		requiredBaseMetrics={requiredBaseMetrics}
		navIdx={drawerNavIdx}
		navTotal={drawerNavTotal}
		onClose={closeActiveDrawer}
		onPrev={() => void navigateActiveDrawer(-1)}
		onNext={() => void navigateActiveDrawer(1)}
	/>
{/if}
