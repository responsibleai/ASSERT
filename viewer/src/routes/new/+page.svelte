<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	/*
	 * New evaluation wizard.
	 *
	 * Hydrates from /api/behaviors, /api/suites, /api/dimensions, /api/models.
	 * On submit, POSTs the collected wizard state to /api/runs, which:
	 *   - validates the payload (returns 400 on errors)
	 *   - reserves artifacts/results/<suite>/<run>/ atomically
	 *   - writes eval_config.yaml (single-YAML authoring; behavior description
	 *     lives inline in behavior.description)
	 *   - spawns `p2m run --config <generated.yaml>` detached
	 * On success the wizard navigates to /suite/<suite>/<run>/monitor.
	 */
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import InfoTooltip from '$lib/components/InfoTooltip.svelte';

	// ── Constants ───────────────────────────────────────────────────
	const STEPS = [
		{ id: 1, label: 'Input specification' },
		{ id: 2, label: 'Category & evaluation set' },
		{ id: 3, label: 'Summary & submit' }
	] as const;

	// Populated from /api/models on mount. Starts empty so the wizard's model
	// dropdowns only list models the operator has actually configured via
	// .env (P2M_MODEL_OPTIONS, OPENAI_MODEL, AZURE_OPENAI_DEPLOYMENT, ...).
	// Each <select> also exposes a "+ Add custom model…" option that prompts
	// the user for an arbitrary LiteLLM-compatible "provider/model" string
	// and appends it to the catalog for the rest of the session.
	let modelOptions = $state<string[]>([]);

	interface ModelCatalogResponse {
		models?: { id?: string }[];
		defaultModel?: string;
	}

	interface KnownBehavior { name: string; definition: string; suiteId: string }
	interface KnownSuite { suite_id: string; behavior_name: string; behavior_category_count: number }
	interface JudgeDimension { name: string; description: string; rubric: string }
	interface EvalDimension { name: string; levels: string[] }

	// ── Catalog data ────────────────────────────────────────────────
	let knownBehaviors = $state<KnownBehavior[]>([]);
	let behaviorsLoading = $state(true);
	let knownSuites = $state<KnownSuite[]>([]);
	let suitesLoading = $state(true);

	// ── Wizard state ────────────────────────────────────────────────
	let currentStep = $state(1);

	// Step 1
	let step1Mode = $state<'select' | 'create'>('select');
	let behaviorSearch = $state('');
	let selectedBehavior = $state<KnownBehavior | null>(null);
	let showDropdown = $state(false);
	let newBehavior = $state({ name: '', definition: '' });
	let step1Touched = $state(false);
	let applicationContext = $state('');
	let evaluationTarget = $state<'model' | 'agent'>('model');
	let systemPrompt = $state('');
	let toolsMode = $state<'simulated' | 'real'>('simulated');
	let simulatedToolsDescription = $state('');
	let realToolsYaml = $state('');
	let realToolsFileName = $state('');
	let realToolsAcknowledged = $state(false);

	// Step 2
	let step2Source = $state<'new' | 'existing' | null>(null);
	let promptTestCasesEnabled = $state(true);
	let promptEvalEnabled = $state(true);
	let scenarioTestCasesEnabled = $state(false);
	let scenarioEvalEnabled = $state(false);
	let systematizeConfig = $state({ model: 'openai/gpt-5.2', behaviorCategoryCount: 25, temperature: 1, maxTokens: 16000, deepResearchAgent: false });
	let systematizeExpanded = $state(true);
	let dimensionBasedExpanded = $state(false);
	let evalDimensions = $state<EvalDimension[]>([]);
	let newDimensionName = $state('');
	let selectedCategories = $state<Set<string>>(new Set());
	let promptTestCasesExpanded = $state(true);
	let promptEvalExpanded = $state(true);
	let scenarioTestCasesExpanded = $state(false);
	let scenarioEvalExpanded = $state(false);
	let promptTestCasesConfig = $state({ model: 'openai/gpt-4.1', budget: 100, temperature: 1, maxTokens: 4000 });
	let promptEvalConfig = $state({ targetModel: 'openai/gpt-4.1', judgeModel: 'openai/gpt-5.2' });
	let scenarioTestCasesConfig = $state({
		modality: 'conversation' as 'conversation' | 'agentic',
		model: 'openai/gpt-4.1',
		budget: 5,
		temperature: 0.2,
		maxTokens: 4000
	});
	let variationDimensions = $state<{ name: string }[]>([
		{ name: 'authority claim' },
		{ name: 'justification' },
		{ name: 'emotional distress' },
		{ name: 'urgency level' },
		{ name: 'authority framing' },
		{ name: 'resource constraints' },
		{ name: 'demographic proxy' }
	]);
	let showAddVariationDim = $state(false);
	let newVariationDim = $state({ name: '', description: '' });
	let scenarioEvalConfig = $state({
		target: { model: 'openai/gpt-4.1', temperature: 0.2, maxTokens: 8000 },
		tester: { model: 'openai/gpt-4.1', temperature: 0.2, maxTokens: 8000 },
		judge: { model: 'openai/gpt-5.2', temperature: 0, maxTokens: 12000, judgePasses: 1 },
		maxTurns: 10
	});
	let judgeDimensions = $state<JudgeDimension[]>([
		{ name: 'overrefusal', description: '', rubric: '' },
		{ name: 'harm actionability', description: '', rubric: '' },
		{ name: 'one sided', description: '', rubric: '' },
		{ name: 'profiling', description: '', rubric: '' }
	]);
	let showAddDimension = $state(false);
	let newDimension = $state({ name: '', description: '', rubric: '' });
	let suiteSearch = $state('');
	let selectedSuite = $state<KnownSuite | null>(null);
	let showSuiteDropdown = $state(false);

	// Step 3
	let suiteId = $state('');
	let runId = $state('v1');
	let submitting = $state(false);
	let submitError = $state('');
	let showDiscardModal = $state(false);
	let isDirty = $state(false);

	function markDirty() {
		isDirty = true;
	}

	// ── Data loading ────────────────────────────────────────────────
	onMount(() => {
		void loadCatalogs();

		const handler = (e: BeforeUnloadEvent) => {
			if (isDirty) {
				e.preventDefault();
				e.returnValue = '';
			}
		};
		window.addEventListener('beforeunload', handler);
		return () => window.removeEventListener('beforeunload', handler);
	});

	async function loadCatalogs() {
		try {
			const [bRes, sRes, dRes, mRes] = await Promise.all([
				fetch('/api/behaviors'),
				fetch('/api/suites'),
				fetch('/api/dimensions'),
				fetch('/api/models')
			]);
			if (bRes.ok) knownBehaviors = await bRes.json();
			behaviorsLoading = false;
			if (sRes.ok) knownSuites = await sRes.json();
			suitesLoading = false;
			if (dRes.ok) {
				const dims = (await dRes.json()) as Record<string, { description?: string; rubric?: string }>;
				const merged = [...judgeDimensions];
				for (const [name, def] of Object.entries(dims)) {
					if (!merged.find((d) => d.name === name)) {
						merged.push({ name, description: def.description ?? '', rubric: def.rubric ?? '' });
					}
				}
				judgeDimensions = merged;
			}
			if (mRes.ok) {
				applyModelCatalog((await mRes.json()) as ModelCatalogResponse);
			}
		} catch {
			behaviorsLoading = false;
			suitesLoading = false;
		}
	}

	function applyModelCatalog(catalog: ModelCatalogResponse) {
		const envModels = (catalog.models ?? [])
			.map((m) => m?.id)
			.filter((id): id is string => typeof id === 'string' && id.trim().length > 0);
		modelOptions = uniqueStrings(envModels);
		if (modelOptions.length === 0) return;
		const desired =
			catalog.defaultModel && modelOptions.includes(catalog.defaultModel)
				? catalog.defaultModel
				: modelOptions[0];
		if (desired && !isDirty) {
			replaceDefaultModels(desired);
		}
	}

	function uniqueStrings(values: string[]): string[] {
		const seen = new Set<string>();
		const out: string[] = [];
		for (const v of values) {
			const trimmed = v.trim();
			if (!trimmed || seen.has(trimmed)) continue;
			seen.add(trimmed);
			out.push(trimmed);
		}
		return out;
	}

	function replaceDefaultModels(model: string) {
		systematizeConfig = { ...systematizeConfig, model };
		promptTestCasesConfig = { ...promptTestCasesConfig, model };
		promptEvalConfig = { targetModel: model, judgeModel: model };
		scenarioTestCasesConfig = { ...scenarioTestCasesConfig, model };
		scenarioEvalConfig = {
			...scenarioEvalConfig,
			target: { ...scenarioEvalConfig.target, model },
			tester: { ...scenarioEvalConfig.tester, model },
			judge: { ...scenarioEvalConfig.judge, model }
		};
	}

	// Sentinel value used by the model <select> dropdowns to expose an
	// "+ Add model" entry. Picking it opens a themed modal for an arbitrary model
	// id, appends it to `modelOptions`, and applies it to the field.
	const ADD_CUSTOM_MODEL = '__add_custom_model__';

	let addModelOpen = $state(false);
	let addModelValue = $state('');
	let addModelError = $state('');
	let pendingApply = $state<((m: string) => void) | null>(null);
	let addModelInput = $state<HTMLInputElement | null>(null);

	$effect(() => {
		if (addModelOpen && addModelInput) {
			addModelInput.focus();
		}
	});

	function handleModelChange(
		event: Event & { currentTarget: HTMLSelectElement },
		currentValue: string,
		apply: (model: string) => void
	) {
		const value = event.currentTarget.value;
		if (value === ADD_CUSTOM_MODEL) {
			event.currentTarget.value = currentValue;
			pendingApply = apply;
			addModelValue = '';
			addModelError = '';
			addModelOpen = true;
			return;
		}
		apply(value);
		markDirty();
	}

	function confirmAddModel() {
		const custom = addModelValue.trim();
		if (!custom) {
			addModelError = 'Model id is required.';
			return;
		}
		if (!/^[a-zA-Z0-9._\/-]+$/.test(custom)) {
			addModelError = 'Use only letters, numbers, “.”, “_”, “-”, and “/”.';
			return;
		}
		if (!modelOptions.includes(custom)) {
			modelOptions = [...modelOptions, custom];
		}
		pendingApply?.(custom);
		markDirty();
		closeAddModel();
	}

	function closeAddModel() {
		addModelOpen = false;
		pendingApply = null;
		addModelValue = '';
		addModelError = '';
	}

	// ── Derived ─────────────────────────────────────────────────────
	let filteredBehaviors = $derived.by(() => {
		const q = behaviorSearch.trim().toLowerCase();
		if (!q) return knownBehaviors;
		return knownBehaviors.filter(
			(b) => b.name.toLowerCase().includes(q) || b.definition.toLowerCase().includes(q)
		);
	});

	let filteredSuites = $derived.by(() => {
		const q = suiteSearch.trim().toLowerCase();
		if (!q) return knownSuites;
		return knownSuites.filter(
			(s) => s.suite_id.toLowerCase().includes(q) || s.behavior_name.toLowerCase().includes(q)
		);
	});

	let step1BehaviorValid = $derived(
		step1Mode === 'select'
			? selectedBehavior !== null
			: newBehavior.name.trim().length > 0 && newBehavior.definition.trim().length > 0
	);
	let step1ContextValid = $derived(applicationContext.trim().length > 0);
	let step1ToolsValid = $derived(
		toolsMode === 'simulated'
			? simulatedToolsDescription.trim().length > 0
			: realToolsYaml.trim().length > 0 && realToolsAcknowledged
	);
	let step1Valid = $derived(step1BehaviorValid && step1ContextValid && step1ToolsValid);
	let step2Valid = $derived.by(() => {
		if (!step2Source) return false;
		if (!promptTestCasesEnabled || !promptEvalEnabled) return false;
		if (step2Source === 'new') return true;
		return selectedSuite !== null;
	});
	let step3Valid = $derived(runId.trim().length > 0);

	function stepValid(s: number) {
		return s === 1 ? step1Valid : s === 2 ? step2Valid : s === 3 ? step3Valid : false;
	}
	function stepReachable(s: number) {
		for (let i = 1; i < s; i++) if (!stepValid(i)) return false;
		return true;
	}

	let summaryRisk = $derived(
		step1Mode === 'select' && selectedBehavior
			? selectedBehavior.name
			: step1Mode === 'create' && newBehavior.name.trim()
				? `${newBehavior.name} (custom)`
				: '— (custom)'
	);
	let summaryTaxonomy = $derived(
		step2Source === 'existing' && selectedSuite
			? `Copied from ${selectedSuite.behavior_name}`
			: step2Source === 'new'
				? 'Behavior categories'
				: '—'
	);
	let summaryTestCasesPipeline = $derived(
		promptTestCasesEnabled
			? `Prompt test cases → ${promptTestCasesConfig.model.split('/')[1]}${dimensionBasedExpanded ? ' (dimension-based)' : ''}`
			: '—'
	);
	let summaryScenarioPipeline = $derived(
		scenarioTestCasesEnabled || scenarioEvalEnabled
			? [scenarioTestCasesEnabled && 'Scenario test cases', scenarioEvalEnabled && 'Scenario eval']
					.filter(Boolean)
					.join(' → ') || '—'
			: '—'
	);

	let dimensionCrossProduct = $derived.by(() => {
		if (!dimensionBasedExpanded) return null;
		const categoryCount = selectedCategories.size || systematizeConfig.behaviorCategoryCount;
		const parts: { name: string; count: number }[] = [
			{ name: 'behavior categories', count: categoryCount }
		];
		for (const f of evalDimensions) {
			const validLevels = f.levels.filter((l) => l.trim().length > 0);
			if (validLevels.length > 0) parts.push({ name: f.name, count: validLevels.length });
		}
		const combinations = parts.reduce((acc, p) => acc * p.count, 1);
		const budget = promptTestCasesConfig.budget;
		const perCombo = combinations > 0 ? Math.max(1, Math.round(budget / combinations)) : budget;
		return { parts, combinations, budget, perCombo };
	});

	// ── Handlers ────────────────────────────────────────────────────
	function goToStep(s: number) {
		if (s >= 1 && s <= 3 && stepReachable(s)) currentStep = s;
	}
	function handleBack() {
		if (currentStep > 1) currentStep -= 1;
	}
	function handleContinue() {
		if (currentStep === 1) step1Touched = true;
		if (currentStep < 3 && stepValid(currentStep)) currentStep += 1;
	}
	function handleCancel() {
		if (isDirty) showDiscardModal = true;
		else goto('/');
	}

	function handleScenarioEvalChange(checked: boolean) {
		scenarioEvalEnabled = checked;
		if (checked) scenarioTestCasesEnabled = true;
		if (!checked) scenarioEvalExpanded = false;
		markDirty();
	}
	function handleScenarioTestCasesChange(checked: boolean) {
		scenarioTestCasesEnabled = checked;
		if (!checked) {
			scenarioEvalEnabled = false;
			scenarioTestCasesExpanded = false;
			scenarioEvalExpanded = false;
		}
		markDirty();
	}

	async function handleSubmit() {
		if (submitting) return;
		submitting = true;
		submitError = '';

		const payload = {
			behavior:
				step1Mode === 'select'
					? { mode: 'existing', name: selectedBehavior?.name, suiteId: selectedBehavior?.suiteId }
					: { mode: 'create', name: newBehavior.name, definition: newBehavior.definition },
			...(applicationContext.trim() ? { applicationContext: applicationContext.trim() } : {}),
			evaluationTarget,
			...(evaluationTarget === 'model' && systemPrompt.trim() ? { systemPrompt: systemPrompt.trim() } : {}),
			source: step2Source,
			...(step2Source === 'existing'
				? { existingSuiteId: selectedSuite?.suite_id }
				: { systematize: { mode: 'quick', config: systematizeConfig } }),
			testCasesPipeline: {
				promptTestCases: promptTestCasesEnabled,
				config: promptTestCasesConfig,
				...(dimensionBasedExpanded
					? {
						dimensionBased: true,
						dimensions: evalDimensions
							.map((f) => ({ name: f.name, levels: f.levels.filter((l) => l.trim()) }))
							.filter((f) => f.levels.length > 0),
						selectedCategories: [...selectedCategories]
					}
					: {})
			},
			inferencePipeline: { promptEval: promptEvalEnabled, config: promptEvalConfig },
			scenarioPipeline: {
				scenarioTestCases: scenarioTestCasesEnabled,
				scenarioEval: scenarioEvalEnabled,
				scenarioTestCasesConfig,
				scenarioEvalConfig,
				variationDimensions,
				judgeDimensions
			},
			suiteId: suiteId.trim() || undefined,
			runId: runId.trim(),
			toolsMode,
			...(toolsMode === 'simulated' && simulatedToolsDescription.trim()
				? { simulatedToolsDescription: simulatedToolsDescription.trim() }
				: {}),
			...(toolsMode === 'real' && realToolsYaml.trim()
				? {
					realToolsYaml,
					...(realToolsFileName ? { realToolsFileName } : {})
				}
				: {})
		};

		let response: Response;
		try {
			response = await fetch('/api/runs', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload)
			});
		} catch (err) {
			submitError = `Network error contacting /api/runs: ${(err as Error).message ?? String(err)}`;
			submitting = false;
			return;
		}

		let body: { suiteId?: string; runId?: string; warnings?: string[]; error?: string; details?: string[] } = {};
		try {
			body = await response.json();
		} catch {
			// fall through with empty body; surface the HTTP status below
		}

		if (!response.ok) {
			const detailText = body?.details?.length ? `\n${body.details.join('\n')}` : '';
			submitError = `${body?.error ?? `HTTP ${response.status}`}${detailText}`;
			submitting = false;
			return;
		}

		if (body?.warnings?.length) {
			// eslint-disable-next-line no-console
			console.warn('[new-eval wizard] backend warnings:', body.warnings);
		}

		isDirty = false;
		const targetSuite = body.suiteId ?? '';
		const targetRun = body.runId ?? runId.trim();
		if (targetSuite && targetRun) {
			void goto(`/suite/${encodeURIComponent(targetSuite)}/${encodeURIComponent(targetRun)}/monitor`);
		} else {
			submitting = false;
			submitError = 'Run started, but the server did not return a suite/run id to navigate to.';
		}
	}

	function addLevel(dIdx: number) {
		const next = evalDimensions.map((f, i) => (i === dIdx ? { ...f, levels: [...f.levels, ''] } : f));
		evalDimensions = next;
	}
	function removeLevel(dIdx: number, lIdx: number) {
		evalDimensions = evalDimensions.map((f, i) =>
			i === dIdx ? { ...f, levels: f.levels.filter((_, li) => li !== lIdx) } : f
		);
	}
	function updateLevel(dIdx: number, lIdx: number, value: string) {
		evalDimensions = evalDimensions.map((f, i) =>
			i === dIdx ? { ...f, levels: f.levels.map((l, li) => (li === lIdx ? value : l)) } : f
		);
	}
	function trimLevels(dIdx: number) {
		evalDimensions = evalDimensions.map((f, i) =>
			i === dIdx ? { ...f, levels: f.levels.filter((l) => l.trim()) } : f
		);
	}
	function removeDimension(dIdx: number) {
		evalDimensions = evalDimensions.filter((_, i) => i !== dIdx);
	}
	function addDimension() {
		const name = newDimensionName.trim();
		if (!name) return;
		evalDimensions = [...evalDimensions, { name, levels: [''] }];
		newDimensionName = '';
	}
	function removeCategory(cat: string) {
		const next = new Set(selectedCategories);
		next.delete(cat);
		selectedCategories = next;
	}
</script>

<!--
	Shared dropdown for any model field in the wizard. Renders Soo's compact
	`form-select` style with one <option> per model. The current value is
	hoisted so a pre-filled default that isn't in the env catalog still shows,
	and a final "+ Add model" entry opens a prompt for a custom id.
-->
{#snippet modelSelect(id: string, currentValue: string, apply: (model: string) => void)}
	{@const opts = Array.from(new Set([currentValue, ...modelOptions].filter(Boolean)))}
	<select {id} class="form-select w-full text-sm" value={currentValue} onchange={(e) => handleModelChange(e, currentValue, apply)}>
		{#each opts as opt}<option value={opt}>{opt}</option>{/each}
		<option value={ADD_CUSTOM_MODEL}>+ Add model</option>
	</select>
{/snippet}

<!-- Breadcrumb -->
<nav class="mb-4" aria-label="Breadcrumb">
	<ol class="Breadcrumb">
		<li class="Breadcrumb-item">
			<a href="/" onclick={(e) => { e.preventDefault(); handleCancel(); }}>Evaluation suites</a>
		</li>
		<li class="Breadcrumb-item" aria-current="page">New evaluation run</li>
	</ol>
</nav>

<div class="wizard-layout">
	<!-- Left nav -->
	<nav class="wizard-nav" aria-label="Wizard steps">
		<ul class="wizard-nav-list" role="list">
			{#each STEPS as step}
				{@const isComplete = stepValid(step.id) && step.id < currentStep}
				{@const isCurrent = step.id === currentStep}
				{@const isReachable = stepReachable(step.id)}
				{@const hasWarning = step.id < currentStep && !stepValid(step.id)}
				<li>
					<button
						class="wizard-nav-item"
						class:wizard-nav-item--active={isCurrent}
						disabled={!isReachable}
						onclick={() => goToStep(step.id)}
						aria-current={isCurrent ? 'step' : undefined}
					>
						<span class="wizard-nav-icon">
							{#if hasWarning}
								<svg class="h-4 w-4 text-score-fail" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M12 9v2m0 4h.01M10.29 3.86l-8.6 14.86A1 1 0 002.54 20h18.92a1 1 0 00.85-1.28l-8.6-14.86a1 1 0 00-1.72 0z"/></svg>
							{:else if isComplete}
								<svg class="h-4 w-4 text-score-pass" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M5 13l4 4L19 7"/></svg>
							{:else}
								<span class="inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-semibold {isCurrent ? 'bg-interactive text-white' : 'bg-surface-2 text-text-muted'}">{step.id}</span>
							{/if}
						</span>
						<span class="wizard-nav-label">{step.label}</span>
					</button>
				</li>
			{/each}
		</ul>
	</nav>

	<!-- Main content -->
	<div class="wizard-main">
		<div class="w-full rounded-lg border border-border bg-surface p-6">
			<!-- ═════════ STEP 1 ═════════ -->
			{#if currentStep === 1}
				<p class="mb-1 text-[16px] font-semibold text-text">Step 1</p>
				<h2 class="mb-1 text-lg font-semibold text-text">Input specification</h2>
				<p class="mb-5 text-sm text-text-muted">Define what is being evaluated and the context in which the evaluation runs.</p>

				<p class="mb-2 text-[16px] font-semibold text-text">Behavior specification</p>

				{#if step1Mode === 'select'}
					<div class="mb-4">
						<label for="behavior-search" class="mb-1 block text-xs font-semibold text-text-secondary">Search behaviors <span class="text-score-fail">*</span></label>
						<div class="flex items-center gap-3">
							<div class="relative flex-1">
								<div class="relative">
									<svg class="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor"><path d="M10.68 11.74a6 6 0 0 1-7.922-8.982 6 6 0 0 1 8.982 7.922l3.04 3.04a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215ZM11.5 7a4.499 4.499 0 1 0-8.997 0A4.499 4.499 0 0 0 11.5 7Z"/></svg>
									<input
										id="behavior-search"
										type="text"
										class="form-control form-control-compact w-full"
										style="padding-left: 2rem;"
										placeholder={behaviorsLoading ? 'Loading behaviors…' : 'Type to search existing behaviors…'}
										value={behaviorSearch}
										oninput={(e) => { behaviorSearch = e.currentTarget.value; showDropdown = true; selectedBehavior = null; step1Touched = true; markDirty(); }}
										onfocus={() => (showDropdown = true)}
										disabled={behaviorsLoading}
										autocomplete="off"
									/>
								</div>
								{#if showDropdown && !selectedBehavior}
									<div class="wizard-dropdown absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-border bg-surface shadow-lg">
										{#if filteredBehaviors.length === 0}
											<div class="px-3 py-3 text-sm text-text-muted">No matching behaviors found.</div>
										{:else}
											<ul class="ActionList" role="listbox">
												{#each filteredBehaviors as b}
													<li class="ActionList-item" role="option" aria-selected="false">
														<button
															class="ActionList-content w-full text-left"
															onclick={() => { selectedBehavior = b; behaviorSearch = b.name; showDropdown = false; step1Touched = true; markDirty(); }}
														>
															<span class="block">
																<span class="text-sm font-medium text-text">{b.name}</span>
																{#if b.definition}
																	<span class="mt-0.5 block line-clamp-1 text-xs text-text-muted">{b.definition}</span>
																{/if}
															</span>
														</button>
													</li>
												{/each}
											</ul>
										{/if}
									</div>
								{/if}
							</div>
							<button
								type="button"
								class="btn shrink-0 whitespace-nowrap"
								onclick={() => { step1Mode = 'create'; selectedBehavior = null; behaviorSearch = ''; showDropdown = false; }}
							>+ New behavior specification</button>
						</div>
					</div>
					{#if selectedBehavior}
						<div class="rounded-md border border-border bg-bg p-4">
							<div class="mb-3 flex items-center justify-between">
								<span class="text-sm font-semibold text-text">{selectedBehavior.name}</span>
								<button class="text-xs text-interactive hover:underline" onclick={() => { selectedBehavior = null; behaviorSearch = ''; step1Touched = false; }}>Clear</button>
							</div>
							<span class="mb-1 block text-[10px] font-semibold text-text-muted">Definition</span>
							<p class="whitespace-pre-wrap text-sm text-text-secondary">{selectedBehavior.definition || 'No definition provided.'}</p>
						</div>
					{:else if step1Touched}
						<p class="mt-1 text-xs text-score-fail">Select an existing behavior, or create a new specification.</p>
					{/if}
					{#if showDropdown}
						<div
							class="fixed inset-0 z-10"
							role="presentation"
							onclick={() => (showDropdown = false)}
						></div>
					{/if}
				{:else}
					<div class="mb-4 flex items-center gap-2">
						<button class="text-xs text-interactive hover:underline" onclick={() => { step1Mode = 'select'; newBehavior = { name: '', definition: '' }; step1Touched = false; }}>← Back to search</button>
					</div>
					<div class="mb-4">
						<label for="new-behavior-name" class="mb-1 block text-xs font-semibold text-text-secondary">Name <span class="text-score-fail">*</span></label>
						<input
							id="new-behavior-name"
							type="text"
							maxlength="150"
							class="form-control w-full text-sm {step1Touched && !newBehavior.name.trim() ? 'border-score-fail' : ''}"
							placeholder="e.g., Harmful content generation"
							value={newBehavior.name}
							oninput={(e) => { newBehavior = { ...newBehavior, name: e.currentTarget.value }; step1Touched = true; markDirty(); }}
						/>
						{#if step1Touched && !newBehavior.name.trim()}
							<p class="mt-1 text-xs text-score-fail">Name is required.</p>
						{:else if newBehavior.name.length > 120}
							<p class="mt-1 text-xs text-text-muted">{newBehavior.name.length} / 150 characters</p>
						{/if}
					</div>
					<div>
						<label for="new-behavior-definition" class="mb-1 block text-xs font-semibold text-text-secondary">Definition <span class="text-score-fail">*</span></label>
						<textarea
							id="new-behavior-definition"
							class="form-control w-full text-sm {step1Touched && !newBehavior.definition.trim() ? 'border-score-fail' : ''}"
							rows="6"
							placeholder="Describe the behavior specification…"
							value={newBehavior.definition}
							oninput={(e) => { newBehavior = { ...newBehavior, definition: e.currentTarget.value }; step1Touched = true; markDirty(); }}
						></textarea>
						{#if step1Touched && !newBehavior.definition.trim()}<p class="mt-1 text-xs text-score-fail">Definition is required.</p>{/if}
					</div>
				{/if}

				<!-- Evaluation target -->
				<div class="mb-1 mt-6">
					<p class="mb-2 text-[16px] font-semibold text-text">Evaluation target</p>
					<div class="mb-1 flex gap-3" style="max-width: 480px;">
						<button
							type="button"
							class="flex-1 rounded-lg border p-3 text-left transition-colors {evaluationTarget === 'model' ? 'border-interactive bg-interactive/10 ring-1 ring-interactive/40' : 'border-border hover:border-text-muted'}"
							onclick={() => { evaluationTarget = 'model'; markDirty(); }}
						>
							<span class="text-sm font-medium text-text">Model</span>
							<span class="block text-xs text-text-muted">Evaluate a model directly.</span>
						</button>
						<button
							type="button"
							class="flex-1 rounded-lg border p-3 text-left transition-colors {evaluationTarget === 'agent' ? 'border-interactive bg-interactive/10 ring-1 ring-interactive/40' : 'border-border hover:border-text-muted'}"
							onclick={() => { evaluationTarget = 'agent'; markDirty(); }}
						>
							<span class="text-sm font-medium text-text">Prompt Agent</span>
							<span class="block text-xs text-text-muted">Evaluate an agent or system.</span>
						</button>
					</div>
				</div>

				<!-- Application context -->
				<div class="mt-6">
					<label for="application-context" class="mb-1 block text-[16px] font-semibold text-text">Application context</label>
					<p class="mb-2 text-xs text-text-muted">Describe the application or agent this evaluation targets (purpose, users, typical interactions). This context is used to ground the evaluation. <span class="text-score-fail">*</span></p>
					<textarea
						id="application-context"
						class="form-control w-full text-sm"
						rows="4"
						placeholder="A conversational medical assistant that provides general health education and safe referral guidance."
						value={applicationContext}
						oninput={(e) => { applicationContext = e.currentTarget.value; step1Touched = true; markDirty(); }}
					></textarea>
					{#if step1Touched && !step1ContextValid}
						<p class="mt-1 text-xs text-score-fail">Application context is required.</p>
					{/if}
				</div>

				<!-- System prompt -->
				{#if evaluationTarget === 'model'}
					<div class="mt-6">
						<label for="system-prompt" class="mb-1 block text-[16px] font-semibold text-text">System prompt <span class="font-normal text-text-muted">(optional)</span></label>
						<p class="mb-2 text-xs text-text-muted">Required when evaluating a model directly. Optional if the prompt is already defined inside the agent.</p>
						<textarea
							id="system-prompt"
							class="form-control w-full text-sm"
							rows="6"
							placeholder="You are a helpful assistant that…"
							value={systemPrompt}
							oninput={(e) => { systemPrompt = e.currentTarget.value; markDirty(); }}
						></textarea>
					</div>
				{/if}

				<!-- Tools -->
				<div class="mt-6">
					<p class="mb-1 text-[16px] font-semibold text-text">Tools</p>
					<p class="mb-2 text-xs text-text-muted">Choose how the target's tools are made available during evaluation.</p>
					<div class="mb-3 flex gap-3" style="max-width: 560px;">
						<button
							type="button"
							class="flex-1 rounded-lg border p-3 text-left transition-colors {toolsMode === 'simulated' ? 'border-interactive bg-interactive/10 ring-1 ring-interactive/40' : 'border-border hover:border-text-muted'}"
							onclick={() => { toolsMode = 'simulated'; markDirty(); }}
						>
							<span class="text-sm font-medium text-text">Simulated tools</span>
							<span class="block text-xs text-text-muted">Describe tools in natural language; the tester simulates results.</span>
						</button>
						<button
							type="button"
							class="flex-1 rounded-lg border p-3 text-left transition-colors {toolsMode === 'real' ? 'border-interactive bg-interactive/10 ring-1 ring-interactive/40' : 'border-border hover:border-text-muted'}"
							onclick={() => { toolsMode = 'real'; markDirty(); }}
						>
							<span class="text-sm font-medium text-text">Real tools</span>
							<span class="block text-xs text-text-muted">Upload a YAML file defining real tool implementations.</span>
						</button>
					</div>

					{#if toolsMode === 'simulated'}
						<div>
							<label for="simulated-tools" class="mb-1 block text-xs text-text-muted">Describe the tools the agent has access to (one per line or freeform). <span class="text-score-fail">*</span></label>
							<textarea
								id="simulated-tools"
								class="form-control w-full text-sm font-mono"
								rows="6"
								placeholder={`e.g. tool_name(arg): short description of what it does and any side effects.`}
								value={simulatedToolsDescription}
								oninput={(e) => { simulatedToolsDescription = e.currentTarget.value; step1Touched = true; markDirty(); }}
							></textarea>
							{#if step1Touched && !step1ToolsValid}
								<p class="mt-1 text-xs text-score-fail">Describe at least one tool, or switch to a different mode.</p>
							{/if}
						</div>
					{:else if toolsMode === 'real'}
						<div
							class="mb-3"
							role="status"
							aria-label="Warning"
							style="display: flex; gap: 0.5rem; align-items: flex-start; padding: 0.5rem 0.75rem; border: 1px solid var(--borderColor-attention-emphasis, #d4a72c); background: var(--bgColor-attention-muted, rgba(212,167,44,0.10)); color: var(--fgColor-default, var(--color-text)); border-radius: 6px; font-size: 0.85rem; line-height: 1.45;"
						>
							<div style="flex: 1 1 auto;">
								<span style="font-weight: 600;">Real tools will be provisioned for your agent.</span>
								<span> The agent may be prompted to take possibly consequential actions. We strongly recommend provisioning the agent in a non-production or sandboxed environment before running this evaluation.</span>
							</div>
						</div>
						<div>
							<label class="mb-1 block text-xs text-text-muted">Upload a YAML file defining your tools (name, description, parameters, boundaries / side-effects).</label>
							<div class="flex items-center gap-3">
								<label class="btn btn-sm cursor-pointer">
									Choose file
									<input
										type="file"
										accept=".yaml,.yml,application/x-yaml,text/yaml,text/plain"
										class="sr-only"
										onchange={async (e) => {
											const file = e.currentTarget.files?.[0];
											if (!file) return;
											realToolsFileName = file.name;
											realToolsYaml = await file.text();
											markDirty();
										}}
									/>
								</label>
								<span class="text-xs text-text-muted">
									{realToolsFileName ? realToolsFileName : 'No file chosen'}
								</span>
								{#if realToolsFileName}
									<button type="button" class="text-xs text-text-muted hover:text-score-fail" onclick={() => { realToolsFileName = ''; realToolsYaml = ''; markDirty(); }}>Clear</button>
								{/if}
							</div>
							{#if realToolsYaml}
								<pre class="mt-3 max-h-48 overflow-y-auto whitespace-pre-wrap break-all rounded border border-border bg-bg/50 p-3 text-[11px] font-mono text-text-secondary">{realToolsYaml}</pre>
							{/if}
						</div>
						<label class="mt-3 flex items-start gap-2 text-xs text-text-secondary">
							<input
								type="checkbox"
								class="primer-checkbox shrink-0 mt-0.5"
								checked={realToolsAcknowledged}
								onchange={(e) => { realToolsAcknowledged = e.currentTarget.checked; step1Touched = true; markDirty(); }}
							/>
							<span>I understand the agent will be granted access to these real tools during evaluation and accept responsibility for the environment it runs in.</span>
						</label>
						{#if step1Touched && !step1ToolsValid}
							<p class="mt-2 text-xs text-score-fail">
								{#if !realToolsYaml.trim()}Upload a YAML file describing your tools.{:else}Confirm the acknowledgement above to continue.{/if}
							</p>
						{/if}
					{/if}
				</div>
			{/if}

			<!-- ═════════ STEP 2 ═════════ -->
			{#if currentStep === 2}
				<p class="mb-1 text-[16px] font-semibold text-text">Step 2</p>
				<h2 class="mb-1 text-lg font-semibold text-text">Category & evaluation set</h2>
				<p class="mb-5 text-sm text-text-muted">Choose how to generate behavior categories and configure evaluation pipelines.</p>

				<div class="mb-6 flex gap-3">
					<button
						class="flex-1 rounded-lg border p-4 text-left transition-colors {step2Source === 'new' ? 'border-interactive bg-interactive/10 ring-1 ring-interactive/40' : 'border-border hover:border-text-muted'}"
						onclick={() => { step2Source = 'new'; markDirty(); }}
					>
						<span class="block text-sm font-semibold text-text">Create new</span>
						<span class="mt-0.5 block text-xs text-text-muted">Generate new categories from behavior specification using AI pipelines.</span>
					</button>
					<button
						class="flex-1 rounded-lg border p-4 text-left transition-colors {step2Source === 'existing' ? 'border-interactive bg-interactive/10 ring-1 ring-interactive/40' : 'border-border hover:border-text-muted'}"
						onclick={() => { step2Source = 'existing'; markDirty(); }}
					>
						<span class="block text-sm font-semibold text-text">Create from existing suite</span>
						<span class="mt-0.5 block text-xs text-text-muted">Select models for pipeline staging from an existing suite's categories.</span>
					</button>
				</div>

				{#if step2Source}
					<!-- Suite picker (existing) -->
					{#if step2Source === 'existing'}
						<div class="mb-6">
							<p class="mb-4 text-sm text-text-muted">Generate new evaluation set (prompt test cases only) from behavior categories in a pre-existing suite.</p>
							<div class="relative mb-4">
								<label for="suite-search" class="mb-1 block text-xs font-semibold text-text-secondary">Select suite <span class="text-score-fail">*</span></label>
								<div class="relative">
									<svg class="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" fill="currentColor"><path d="M10.68 11.74a6 6 0 0 1-7.922-8.982 6 6 0 0 1 8.982 7.922l3.04 3.04a.749.749 0 0 1-.326 1.275.749.749 0 0 1-.734-.215ZM11.5 7a4.499 4.499 0 1 0-8.997 0A4.499 4.499 0 0 0 11.5 7Z"/></svg>
									<input
										id="suite-search"
										type="text"
										class="form-control w-full"
										style="padding-left: 2rem;"
										placeholder={suitesLoading ? 'Loading suites…' : 'Search suites…'}
										value={suiteSearch}
										oninput={(e) => { suiteSearch = e.currentTarget.value; showSuiteDropdown = true; selectedSuite = null; markDirty(); }}
										onfocus={() => (showSuiteDropdown = true)}
										disabled={suitesLoading}
										autocomplete="off"
									/>
								</div>
								{#if showSuiteDropdown && !selectedSuite}
									<div class="wizard-dropdown absolute z-20 mt-1 max-h-56 w-full overflow-y-auto rounded-lg border border-border bg-surface shadow-lg">
										{#if filteredSuites.length === 0}
											<div class="px-3 py-3 text-sm text-text-muted">No matching suites found.</div>
										{:else}
											<ul class="ActionList" role="listbox">
												{#each filteredSuites as s}
													<li class="ActionList-item" role="option" aria-selected="false">
														<button class="ActionList-content w-full text-left" onclick={() => { selectedSuite = s; suiteSearch = s.behavior_name; showSuiteDropdown = false; markDirty(); }}>
														<span class="block min-w-0">
															<span class="block break-words text-sm font-medium text-text">{s.behavior_name}</span>
															<span class="mt-0.5 block break-words text-xs text-text-muted">{s.suite_id} · {s.behavior_category_count} categories</span>
														</span>
													</button>
													</li>
												{/each}
											</ul>
										{/if}
									</div>
								{/if}
							</div>
							{#if selectedSuite}
								<div class="rounded-md border border-border bg-bg p-4">
									<div class="mb-2 flex items-start justify-between gap-3">
										<span class="min-w-0 break-words text-sm font-semibold text-text">{selectedSuite.behavior_name}</span>
										<button class="shrink-0 text-xs text-interactive hover:underline" onclick={() => { selectedSuite = null; suiteSearch = ''; }}>Clear</button>
									</div>
									<p class="break-words text-xs text-text-muted">{selectedSuite.suite_id} · {selectedSuite.behavior_category_count} behavior categories</p>
								</div>
							{/if}
							{#if showSuiteDropdown}
								<div class="fixed inset-0 z-10" role="presentation" onclick={() => (showSuiteDropdown = false)}></div>
							{/if}
						</div>
					{/if}

					<!-- Behavior categories pipeline (new mode) -->
					<p class="mb-2 text-[16px] font-semibold text-text">Evaluation pipeline</p>
					{#if step2Source === 'new'}
						<div class="mb-5">
							<p class="mb-2 text-xs font-semibold text-text-muted">Behavior systematization</p>
							<div class="rounded-lg border border-border">
								<div class="p-3">
									<div
										class="pipeline-row"
										role="button"
										tabindex="0"
										aria-expanded={systematizeExpanded}
										onclick={() => (systematizeExpanded = !systematizeExpanded)}
										onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); systematizeExpanded = !systematizeExpanded; } }}
									>
										<div class="pipeline-row-label">
											<span class="flex items-center gap-1.5 text-sm font-medium text-text">
												Behavior categories
												{#if systematizeConfig.deepResearchAgent}
													<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="text-interactive" aria-label="Deep Research Agent enabled"><title>Deep Research Agent enabled</title><path d="M7.53 1.282a.5.5 0 0 1 .94 0l.478 1.306a4 4 0 0 0 2.384 2.385l1.307.478a.5.5 0 0 1 0 .938l-1.307.478a4 4 0 0 0-2.384 2.385l-.478 1.306a.5.5 0 0 1-.94 0l-.478-1.306a4 4 0 0 0-2.385-2.385L3.36 6.389a.5.5 0 0 1 0-.938l1.307-.478A4 4 0 0 0 7.053 2.59l.478-1.307Zm5.49 7.078a.25.25 0 0 1 .47 0l.279.763a2.25 2.25 0 0 0 1.342 1.342l.762.279a.25.25 0 0 1 0 .469l-.762.279a2.25 2.25 0 0 0-1.342 1.342l-.28.762a.25.25 0 0 1-.469 0l-.279-.762a2.25 2.25 0 0 0-1.342-1.342l-.762-.28a.25.25 0 0 1 0-.469l.762-.279a2.25 2.25 0 0 0 1.342-1.342l.28-.762Z"/></svg>
												{/if}
											</span>
											<span class="text-xs text-text-muted">Generate behavior categories from behavior definition.</span>
										</div>
										<span class="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-medium text-text-muted">{systematizeConfig.model.split('/')[1]}</span>
										<span class="pipeline-row-chevron">
											<svg class="h-4 w-4 transition-transform {systematizeExpanded ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M19 9l-7 7-7-7"/></svg>
										</span>
									</div>
									{#if systematizeExpanded}
										<div class="mt-3 grid gap-3" style="max-width: 560px; grid-template-columns: 1.8fr 1fr 1fr 1fr;">
											<div>
												<label for="tax-model" class="mb-0.5 block text-[10px] text-text-muted">Model</label>
												{@render modelSelect('tax-model', systematizeConfig.model, (v) => (systematizeConfig = { ...systematizeConfig, model: v }))}
											</div>
											<div>
												<label for="tax-budget" class="mb-0.5 block text-[10px] text-text-muted">Categories</label>
												<input id="tax-budget" type="number" step="1" min="1" class="form-control w-full text-sm" value={systematizeConfig.behaviorCategoryCount} oninput={(e) => { systematizeConfig = { ...systematizeConfig, behaviorCategoryCount: Number(e.currentTarget.value) }; markDirty(); }} />
											</div>
											<div>
												<label for="tax-temp" class="mb-0.5 block text-[10px] text-text-muted">Temperature</label>
												<input id="tax-temp" type="number" step="0.1" min="0" max="2" class="form-control w-full text-sm" value={systematizeConfig.temperature} oninput={(e) => { systematizeConfig = { ...systematizeConfig, temperature: Number(e.currentTarget.value) }; markDirty(); }} />
											</div>
											<div>
												<label for="tax-tokens" class="mb-0.5 block text-[10px] text-text-muted">Max tokens</label>
												<input id="tax-tokens" type="number" step="500" min="1" class="form-control w-full text-sm" value={systematizeConfig.maxTokens} oninput={(e) => { systematizeConfig = { ...systematizeConfig, maxTokens: Number(e.currentTarget.value) }; markDirty(); }} />
											</div>
										</div>
										<label class="mt-3 flex items-start gap-2 text-xs text-text-secondary" style="max-width: 560px;">
											<input
												type="checkbox"
												class="primer-checkbox shrink-0 mt-0.5"
												checked={systematizeConfig.deepResearchAgent}
												onchange={(e) => { systematizeConfig = { ...systematizeConfig, deepResearchAgent: e.currentTarget.checked }; markDirty(); }}
											/>
											<span class="flex flex-col gap-0.5">
												<span class="flex items-center gap-1.5 text-text">
													<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="14" height="14" fill="currentColor" class="text-interactive shrink-0" aria-hidden="true"><path d="M7.53 1.282a.5.5 0 0 1 .94 0l.478 1.306a4 4 0 0 0 2.384 2.385l1.307.478a.5.5 0 0 1 0 .938l-1.307.478a4 4 0 0 0-2.384 2.385l-.478 1.306a.5.5 0 0 1-.94 0l-.478-1.306a4 4 0 0 0-2.385-2.385L3.36 6.389a.5.5 0 0 1 0-.938l1.307-.478A4 4 0 0 0 7.053 2.59l.478-1.307Zm5.49 7.078a.25.25 0 0 1 .47 0l.279.763a2.25 2.25 0 0 0 1.342 1.342l.762.279a.25.25 0 0 1 0 .469l-.762.279a2.25 2.25 0 0 0-1.342 1.342l-.28.762a.25.25 0 0 1-.469 0l-.279-.762a2.25 2.25 0 0 0-1.342-1.342l-.762-.28a.25.25 0 0 1 0-.469l.762-.279a2.25 2.25 0 0 0 1.342-1.342l.28-.762Z"/></svg>
													<span class="font-medium">Use Deep Research Agent</span>
													<InfoTooltip direction="se" label="Systematize the behavior by 1) combining a research-grounded analysis, 2) a simulated expert (Delphi-style) discussion to surface how the behavior manifests in GenAI, and 3) a validator that refines outputs using social science–based criteria. The output is a set of behavior categories." />
												</span>
												<span class="text-text-muted">Slower and more expensive, but produces a more thorough, research-grounded set of behavior categories.</span>
											</span>
										</label>
									{/if}
								</div>
							</div>
						</div>
					{/if}

					<!-- Evaluation set generation pipeline -->
					<div class="mb-5">
						<p class="mb-2 text-xs font-semibold text-text-muted">Evaluation set generation</p>
						<div class="rounded-lg border border-border">
							<div class="p-3">
								<div
									class="pipeline-row"
									role="button"
									tabindex="0"
									aria-expanded={promptTestCasesExpanded}
									onclick={() => (promptTestCasesExpanded = !promptTestCasesExpanded)}
									onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); promptTestCasesExpanded = !promptTestCasesExpanded; } }}
								>
									<div class="pipeline-row-label">
										<span class="block text-sm font-medium text-text">Prompt test cases</span>
										<span class="text-xs text-text-muted">Create test prompts across categories.</span>
									</div>
									<span class="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-medium text-text-muted">{promptTestCasesConfig.model.split('/')[1]}</span>
									<span class="pipeline-row-chevron">
										<svg class="h-4 w-4 transition-transform {promptTestCasesExpanded ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M19 9l-7 7-7-7"/></svg>
									</span>
								</div>
								{#if promptTestCasesExpanded}
									<div class="mt-3 grid gap-3" style="max-width: 560px; grid-template-columns: 1.8fr 1fr 1fr 1fr;">
										<div>
											<label for="qs-model" class="mb-0.5 block text-[10px] text-text-muted">Model</label>
											{@render modelSelect('qs-model', promptTestCasesConfig.model, (v) => (promptTestCasesConfig = { ...promptTestCasesConfig, model: v }))}
										</div>
										<div>
											<label for="qs-budget" class="mb-0.5 block text-[10px] text-text-muted">Test set size</label>
											<input id="qs-budget" type="number" step="10" min="1" class="form-control w-full text-sm" value={promptTestCasesConfig.budget} oninput={(e) => { promptTestCasesConfig = { ...promptTestCasesConfig, budget: Number(e.currentTarget.value) }; markDirty(); }} />
										</div>
										<div>
											<label for="qs-temp" class="mb-0.5 block text-[10px] text-text-muted">Temperature</label>
											<input id="qs-temp" type="number" step="0.1" min="0" max="2" class="form-control w-full text-sm" value={promptTestCasesConfig.temperature} oninput={(e) => { promptTestCasesConfig = { ...promptTestCasesConfig, temperature: Number(e.currentTarget.value) }; markDirty(); }} />
										</div>
										<div>
											<label for="qs-tokens" class="mb-0.5 block text-[10px] text-text-muted">Max tokens</label>
											<input id="qs-tokens" type="number" step="500" min="1" class="form-control w-full text-sm" value={promptTestCasesConfig.maxTokens} oninput={(e) => { promptTestCasesConfig = { ...promptTestCasesConfig, maxTokens: Number(e.currentTarget.value) }; markDirty(); }} />
										</div>
									</div>

									<!-- Advanced: dimension-based -->
									<div class="mt-3" style="max-width: 600px;">
										<button type="button" class="flex items-center gap-1.5 text-xs font-medium text-text transition-colors hover:text-text" onclick={() => (dimensionBasedExpanded = !dimensionBasedExpanded)}>
											<svg class="h-3.5 w-3.5 transition-transform {dimensionBasedExpanded ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
											Advanced: Test set dimension-based prompt generation
										</button>
										{#if dimensionBasedExpanded}
											<div class="mt-3 space-y-4 rounded-lg border border-border bg-bg p-4">
												<p class="text-xs text-text-muted">Generate prompts as a cross-product of test set dimensions. Each dimension defines axes of variation; prompts are generated for every combination.</p>
												{#each evalDimensions as dimension, dIdx}
													<div class="rounded-md border border-border p-3">
														<div class="mb-2 flex items-center justify-between">
															<span class="text-xs font-semibold text-text">{dimension.name}</span>
															<button type="button" class="text-xs text-text-muted transition-colors hover:text-score-fail" onclick={() => removeDimension(dIdx)}>
																<svg class="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6 18L18 6M6 6l12 12"/></svg>
															</button>
														</div>
														<div class="mb-2 flex flex-wrap gap-1.5">
															{#each dimension.levels as level, lIdx}
																{#if level.trim()}
																	<span class="dim-chip cursor-pointer">
																		{level}
																		<button type="button" class="ml-0.5 text-text-muted transition-colors hover:text-score-fail" onclick={() => removeLevel(dIdx, lIdx)}>
																			<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6 18L18 6M6 6l12 12"/></svg>
																		</button>
																	</span>
																{:else}
																	<span class="level-edit-wrap">
																		<!-- svelte-ignore a11y_autofocus -->
																		<input
																			type="text"
																			class="form-control"
																			placeholder={`Level ${lIdx + 1}`}
																			autofocus
																			value={level}
																			oninput={(e) => updateLevel(dIdx, lIdx, e.currentTarget.value)}
																			onkeydown={(e) => { if (e.key === 'Enter') (e.currentTarget as HTMLInputElement).blur(); if (e.key === 'Escape') removeLevel(dIdx, lIdx); }}
																			onblur={() => trimLevels(dIdx)}
																		/>
																	</span>
																{/if}
															{/each}
															<button type="button" class="dim-chip dim-chip-new" onclick={() => addLevel(dIdx)}>+ Level</button>
														</div>
													</div>
												{/each}
												<div class="flex items-center gap-2">
													<input
														type="text"
														class="form-control text-sm"
														style="min-width: 240px;"
														placeholder="Test set dimension name"
														value={newDimensionName}
														oninput={(e) => (newDimensionName = e.currentTarget.value)}
														onkeydown={(e) => { if (e.key === 'Enter' && newDimensionName.trim()) addDimension(); }}
													/>
													<button class="btn" disabled={!newDimensionName.trim()} onclick={addDimension}>Add</button>
												</div>
											</div>
										{/if}
									</div>
								{/if}
							</div>
						</div>
					</div>

					<!-- Rollout and judge pipeline -->
					<div class="mb-5">
						<p class="mb-2 text-xs font-semibold text-text-muted">Inference and score</p>
						<div class="rounded-lg border border-border">
							<div class="p-3">
								<div
									class="pipeline-row"
									role="button"
									tabindex="0"
									aria-expanded={promptEvalExpanded}
									onclick={() => (promptEvalExpanded = !promptEvalExpanded)}
									onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); promptEvalExpanded = !promptEvalExpanded; } }}
								>
									<div class="pipeline-row-label">
										<span class="block text-sm font-medium text-text">Prompt evaluation</span>
										<span class="text-xs text-text-muted">Run prompt test cases against target model and judge responses.</span>
									</div>
									<span class="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-medium text-text-muted">{promptEvalConfig.targetModel.split('/')[1]}</span>
									<span class="pipeline-row-chevron">
										<svg class="h-4 w-4 transition-transform {promptEvalExpanded ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M19 9l-7 7-7-7"/></svg>
									</span>
								</div>
								{#if promptEvalExpanded}
									<div class="mt-3 grid grid-cols-2 gap-3" style="max-width: 360px;">
										<div>
											<label for="pe-target" class="mb-0.5 block text-[10px] text-text-muted">Target model</label>
											{@render modelSelect('pe-target', promptEvalConfig.targetModel, (v) => (promptEvalConfig = { ...promptEvalConfig, targetModel: v }))}
										</div>
										<div>
											<label for="pe-judge" class="mb-0.5 block text-[10px] text-text-muted">Judge model</label>
											{@render modelSelect('pe-judge', promptEvalConfig.judgeModel, (v) => (promptEvalConfig = { ...promptEvalConfig, judgeModel: v }))}
										</div>
									</div>
								{/if}
							</div>
						</div>
					</div>

					<!-- Audit pipeline -->
					<div class="mb-5">
						<div class="divide-y divide-border rounded-lg border border-border">
							<!-- Scenario test cases row -->
							<div class="p-3">
								<div class="flex items-center gap-3">
									<input
										type="checkbox"
										class="primer-checkbox shrink-0"
										checked={scenarioTestCasesEnabled}
										onchange={(e) => { handleScenarioTestCasesChange(e.currentTarget.checked); if (e.currentTarget.checked) scenarioTestCasesExpanded = true; }}
										onclick={(e) => e.stopPropagation()}
									/>
									<div
										class="pipeline-row"
										role="button"
										tabindex="0"
										aria-expanded={scenarioTestCasesExpanded}
										onclick={() => (scenarioTestCasesExpanded = !scenarioTestCasesExpanded)}
										onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); scenarioTestCasesExpanded = !scenarioTestCasesExpanded; } }}
									>
										<div class="pipeline-row-label">
											<span class="block text-sm font-medium text-text">Scenario test cases <span class="font-normal text-text-muted">(optional)</span></span>
											<span class="text-xs text-text-muted">Generate multi-turn scenario test cases.</span>
										</div>
										<span class="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-medium text-text-muted">{scenarioTestCasesConfig.model.split('/')[1]}</span>
										<span class="pipeline-row-chevron">
											<svg class="h-4 w-4 transition-transform {scenarioTestCasesExpanded ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M19 9l-7 7-7-7"/></svg>
										</span>
									</div>
								</div>
								{#if scenarioTestCasesExpanded}
									<div class="ml-7 mt-3 space-y-4" style="max-width: 600px;">
										<div>
											<div class="mb-1 block text-[10px] text-text-muted">Modality</div>
											<div class="wizard-seg" style="max-width: 240px;" role="radiogroup" aria-label="Modality">
												{#each ['conversation', 'agentic'] as m}
													<button
														type="button"
														role="radio"
														aria-checked={scenarioTestCasesConfig.modality === m}
														class="wizard-seg-btn"
														class:selected={scenarioTestCasesConfig.modality === m}
														onclick={() => { scenarioTestCasesConfig = { ...scenarioTestCasesConfig, modality: m as 'conversation' | 'agentic' }; markDirty(); }}
													>{m.charAt(0).toUpperCase() + m.slice(1)}</button>
												{/each}
											</div>
											<p class="mt-1 text-[10px] text-text-muted">
												{scenarioTestCasesConfig.modality === 'conversation'
													? 'Standard multi-turn conversation between tester and target.'
													: 'Agentic multi-turn interaction with tool-use capabilities.'}
											</p>
										</div>
										<div class="grid grid-cols-4 gap-3">
											<div>
												<label for="as-model" class="mb-0.5 block text-[10px] text-text-muted">Model</label>
												{@render modelSelect('as-model', scenarioTestCasesConfig.model, (v) => (scenarioTestCasesConfig = { ...scenarioTestCasesConfig, model: v }))}
											</div>
											<div>
												<label for="as-budget" class="mb-0.5 block text-[10px] text-text-muted">Test set size</label>
												<input id="as-budget" type="number" min="1" class="form-control w-full text-sm" value={scenarioTestCasesConfig.budget} oninput={(e) => { scenarioTestCasesConfig = { ...scenarioTestCasesConfig, budget: Number(e.currentTarget.value) }; markDirty(); }} />
											</div>
											<div>
												<label for="as-temp" class="mb-0.5 block text-[10px] text-text-muted">Temperature</label>
												<input id="as-temp" type="number" step="0.1" min="0" max="2" class="form-control w-full text-sm" value={scenarioTestCasesConfig.temperature} oninput={(e) => { scenarioTestCasesConfig = { ...scenarioTestCasesConfig, temperature: Number(e.currentTarget.value) }; markDirty(); }} />
											</div>
											<div>
												<label for="as-tokens" class="mb-0.5 block text-[10px] text-text-muted">Max tokens</label>
												<input id="as-tokens" type="number" step="500" min="1" class="form-control w-full text-sm" value={scenarioTestCasesConfig.maxTokens} oninput={(e) => { scenarioTestCasesConfig = { ...scenarioTestCasesConfig, maxTokens: Number(e.currentTarget.value) }; markDirty(); }} />
											</div>
										</div>
										<div>
											<div class="mb-1 block text-[10px] text-text-muted">Variation dimensions <span class="opacity-60">(optional — generate seed variants along each axis)</span></div>
											<div class="flex flex-wrap gap-1.5">
												{#each variationDimensions as dim, i}
													<span class="dim-chip">
														{dim.name}
														<button type="button" class="ml-0.5 text-text-muted transition-colors hover:text-score-fail" onclick={() => (variationDimensions = variationDimensions.filter((_, idx) => idx !== i))}>
															<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6 18L18 6M6 6l12 12"/></svg>
														</button>
													</span>
												{/each}
												<button type="button" class="dim-chip dim-chip-new" onclick={() => (showAddVariationDim = true)}>+ New</button>
											</div>
											{#if showAddVariationDim}
												<div class="mt-3 space-y-2 rounded-lg border border-border bg-bg p-3">
													<div>
														<label for="vdim-name" class="mb-1 block text-xs font-semibold text-text-secondary">Name</label>
														<!-- svelte-ignore a11y_autofocus -->
														<input id="vdim-name" type="text" class="form-control w-full text-sm" placeholder="e.g. emotional_pressure" autofocus value={newVariationDim.name} oninput={(e) => (newVariationDim = { ...newVariationDim, name: e.currentTarget.value })} />
													</div>
													<div>
														<label for="vdim-desc" class="mb-1 block text-xs font-semibold text-text-secondary">Description</label>
														<textarea id="vdim-desc" class="form-control w-full text-sm" rows="3" placeholder="What to vary and what to keep fixed." value={newVariationDim.description} oninput={(e) => (newVariationDim = { ...newVariationDim, description: e.currentTarget.value })}></textarea>
													</div>
													<div class="flex items-center gap-2">
														<button class="btn btn-primary" disabled={!newVariationDim.name.trim()} onclick={() => { variationDimensions = [...variationDimensions, { name: newVariationDim.name.trim() }]; newVariationDim = { name: '', description: '' }; showAddVariationDim = false; }}>Add dimension</button>
														<button class="btn" onclick={() => { showAddVariationDim = false; newVariationDim = { name: '', description: '' }; }}>Cancel</button>
													</div>
												</div>
											{/if}
										</div>
									</div>
								{/if}
							</div>

							<!-- Scenario eval row -->
							<div class="p-3">
								<div class="flex items-center gap-3">
									<input
										type="checkbox"
										class="primer-checkbox shrink-0"
										checked={scenarioEvalEnabled}
										onchange={(e) => { handleScenarioEvalChange(e.currentTarget.checked); if (e.currentTarget.checked) scenarioEvalExpanded = true; }}
										onclick={(e) => e.stopPropagation()}
									/>
									<div
										class="pipeline-row"
										role="button"
										tabindex="0"
										aria-expanded={scenarioEvalExpanded}
										onclick={() => (scenarioEvalExpanded = !scenarioEvalExpanded)}
										onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); scenarioEvalExpanded = !scenarioEvalExpanded; } }}
									>
										<div class="pipeline-row-label">
											<span class="block text-sm font-medium text-text">Scenario evaluation <span class="font-normal text-text-muted">(optional)</span></span>
											<span class="text-xs text-text-muted">Multi-turn red-team tester against target model.</span>
										</div>
										<span class="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-[10px] font-medium text-text-muted">{scenarioEvalConfig.target.model.split('/')[1]}</span>
										<span class="pipeline-row-chevron">
											<svg class="h-4 w-4 transition-transform {scenarioEvalExpanded ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M19 9l-7 7-7-7"/></svg>
										</span>
									</div>
								</div>
								{#if scenarioEvalExpanded}
									<div class="ml-7 mt-3 space-y-4" style="max-width: 620px;">
										<div style="max-width: 120px;">
											<label for="se-max-turns" class="mb-0.5 block text-[10px] text-text-muted">Max turns</label>
											<input id="se-max-turns" type="number" step="1" min="1" class="form-control w-full text-sm" value={scenarioEvalConfig.maxTurns} oninput={(e) => { scenarioEvalConfig = { ...scenarioEvalConfig, maxTurns: Number(e.currentTarget.value) }; markDirty(); }} />
										</div>
										<div>
											<span class="mb-1.5 block text-[10px] font-semibold text-text-muted">Target</span>
											<div class="grid gap-3" style="grid-template-columns: minmax(220px, 1.6fr) 1fr 1fr;">
												<div>
													<label for="se-target-model" class="mb-0.5 block text-[10px] text-text-muted">Model</label>
													{@render modelSelect('se-target-model', scenarioEvalConfig.target.model, (v) => (scenarioEvalConfig = { ...scenarioEvalConfig, target: { ...scenarioEvalConfig.target, model: v } }))}
												</div>
												<div>
													<label for="se-target-temp" class="mb-0.5 block text-[10px] text-text-muted">Temperature</label>
													<input id="se-target-temp" type="number" step="0.1" min="0" max="2" class="form-control w-full text-sm" value={scenarioEvalConfig.target.temperature} oninput={(e) => { scenarioEvalConfig = { ...scenarioEvalConfig, target: { ...scenarioEvalConfig.target, temperature: Number(e.currentTarget.value) } }; markDirty(); }} />
												</div>
												<div>
													<label for="se-target-tokens" class="mb-0.5 block text-[10px] text-text-muted">Max tokens</label>
													<input id="se-target-tokens" type="number" step="1000" min="1" class="form-control w-full text-sm" value={scenarioEvalConfig.target.maxTokens} oninput={(e) => { scenarioEvalConfig = { ...scenarioEvalConfig, target: { ...scenarioEvalConfig.target, maxTokens: Number(e.currentTarget.value) } }; markDirty(); }} />
												</div>
											</div>
										</div>
										<div>
											<span class="mb-1.5 block text-[10px] font-semibold text-text-muted">Tester</span>
											<div class="grid gap-3" style="grid-template-columns: minmax(220px, 1.6fr) 1fr 1fr;">
												<div>
													<label for="se-tester-model" class="mb-0.5 block text-[10px] text-text-muted">Model</label>
													{@render modelSelect('se-tester-model', scenarioEvalConfig.tester.model, (v) => (scenarioEvalConfig = { ...scenarioEvalConfig, tester: { ...scenarioEvalConfig.tester, model: v } }))}
												</div>
												<div>
													<label for="se-tester-temp" class="mb-0.5 block text-[10px] text-text-muted">Temperature</label>
													<input id="se-tester-temp" type="number" step="0.1" min="0" max="2" class="form-control w-full text-sm" value={scenarioEvalConfig.tester.temperature} oninput={(e) => { scenarioEvalConfig = { ...scenarioEvalConfig, tester: { ...scenarioEvalConfig.tester, temperature: Number(e.currentTarget.value) } }; markDirty(); }} />
												</div>
												<div>
													<label for="se-tester-tokens" class="mb-0.5 block text-[10px] text-text-muted">Max tokens</label>
													<input id="se-tester-tokens" type="number" step="1000" min="1" class="form-control w-full text-sm" value={scenarioEvalConfig.tester.maxTokens} oninput={(e) => { scenarioEvalConfig = { ...scenarioEvalConfig, tester: { ...scenarioEvalConfig.tester, maxTokens: Number(e.currentTarget.value) } }; markDirty(); }} />
												</div>
											</div>
										</div>
										<div>
											<span class="mb-1.5 block text-[10px] font-semibold text-text-muted">Judge</span>
											<div class="grid gap-3" style="grid-template-columns: minmax(220px, 1.6fr) 1fr 1fr 1fr;">
												<div>
													<label for="se-judge-model" class="mb-0.5 block text-[10px] text-text-muted">Model</label>
													{@render modelSelect('se-judge-model', scenarioEvalConfig.judge.model, (v) => (scenarioEvalConfig = { ...scenarioEvalConfig, judge: { ...scenarioEvalConfig.judge, model: v } }))}
												</div>
												<div>
													<label for="se-judge-temp" class="mb-0.5 block text-[10px] text-text-muted">Temperature</label>
													<input id="se-judge-temp" type="number" step="0.1" min="0" max="2" class="form-control w-full text-sm" value={scenarioEvalConfig.judge.temperature} oninput={(e) => { scenarioEvalConfig = { ...scenarioEvalConfig, judge: { ...scenarioEvalConfig.judge, temperature: Number(e.currentTarget.value) } }; markDirty(); }} />
												</div>
												<div>
													<label for="se-judge-tokens" class="mb-0.5 block text-[10px] text-text-muted">Max tokens</label>
													<input id="se-judge-tokens" type="number" step="1000" min="1" class="form-control w-full text-sm" value={scenarioEvalConfig.judge.maxTokens} oninput={(e) => { scenarioEvalConfig = { ...scenarioEvalConfig, judge: { ...scenarioEvalConfig.judge, maxTokens: Number(e.currentTarget.value) } }; markDirty(); }} />
												</div>
												<div>
													<label for="se-judge-passes" class="mb-0.5 block text-[10px] text-text-muted">Judge passes</label>
													<input id="se-judge-passes" type="number" step="1" min="1" class="form-control w-full text-sm" value={scenarioEvalConfig.judge.judgePasses} oninput={(e) => { scenarioEvalConfig = { ...scenarioEvalConfig, judge: { ...scenarioEvalConfig.judge, judgePasses: Number(e.currentTarget.value) } }; markDirty(); }} />
												</div>
											</div>
											<div class="mt-2">
												<span class="mb-1.5 block text-[10px] text-text-muted">Dimensions <span class="opacity-60">(optional)</span></span>
												<div class="flex flex-wrap gap-1.5">
													{#each judgeDimensions as dim, idx}
														<span class="dim-chip">
															{dim.name}
															<button type="button" class="ml-0.5 text-text-muted transition-colors hover:text-score-fail" onclick={() => (judgeDimensions = judgeDimensions.filter((_, i) => i !== idx))} aria-label={`Remove ${dim.name}`}>
																<svg class="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6 18L18 6M6 6l12 12"/></svg>
															</button>
														</span>
													{/each}
													<button type="button" class="dim-chip dim-chip-new" onclick={() => (showAddDimension = true)}>+ New</button>
												</div>
												{#if showAddDimension}
													<div class="mt-3 space-y-2 rounded-lg border border-border bg-bg p-3">
														<div>
															<label for="dim-name" class="mb-1 block text-xs font-semibold text-text-secondary">Name</label>
															<input id="dim-name" type="text" class="form-control w-full text-sm" placeholder="e.g. helpfulness" value={newDimension.name} oninput={(e) => (newDimension = { ...newDimension, name: e.currentTarget.value })} />
														</div>
														<div>
															<label for="dim-desc" class="mb-1 block text-xs font-semibold text-text-secondary">Description</label>
															<input id="dim-desc" type="text" class="form-control w-full text-sm" placeholder="How helpful the response is" value={newDimension.description} oninput={(e) => (newDimension = { ...newDimension, description: e.currentTarget.value })} />
														</div>
														<div>
															<label for="dim-rubric" class="mb-1 block text-xs font-semibold text-text-secondary">Rubric (1-3 scale)</label>
															<textarea id="dim-rubric" class="form-control w-full text-sm" rows="3" placeholder={"1 = unhelpful\n2 = somewhat helpful\n3 = very helpful"} value={newDimension.rubric} oninput={(e) => (newDimension = { ...newDimension, rubric: e.currentTarget.value })}></textarea>
														</div>
														<div class="flex items-center gap-2">
															<button class="btn btn-primary" disabled={!newDimension.name.trim()} onclick={() => { judgeDimensions = [...judgeDimensions, { ...newDimension }]; newDimension = { name: '', description: '', rubric: '' }; showAddDimension = false; }}>Add dimension</button>
															<button class="btn" onclick={() => { showAddDimension = false; newDimension = { name: '', description: '', rubric: '' }; }}>Cancel</button>
														</div>
													</div>
												{/if}
											</div>
										</div>
									</div>
								{/if}
							</div>
						</div>
					</div>
				{/if}
			{/if}

			<!-- ═════════ STEP 3 ═════════ -->
			{#if currentStep === 3}
				<p class="mb-1 text-[16px] font-semibold text-text">Step 3</p>
				<h2 class="mb-1 text-lg font-semibold text-text">Summary & submit</h2>
				<p class="mb-5 text-sm text-text-muted">Review your configuration and submit the evaluation run.</p>

				<div class="mb-6">
					<h3 class="mb-3 text-base font-semibold text-text">Summary</h3>
					<div class="space-y-2.5 rounded-md border border-border bg-bg p-4 text-sm">
						<div class="flex items-baseline justify-between gap-3">
							<span class="shrink-0 text-text-muted">Behavior</span>
							<span class="min-w-0 break-words text-right font-medium text-text">{summaryRisk}</span>
						</div>
						{#if applicationContext.trim()}
							<div><span class="mb-0.5 block text-text-muted">Application context</span><span class="block whitespace-pre-wrap break-words text-text">{applicationContext.trim()}</span></div>
						{/if}
						{#if evaluationTarget === 'model' && systemPrompt.trim()}
							<div><span class="mb-0.5 block text-text-muted">System prompt</span><span class="block whitespace-pre-wrap break-words text-text">{systemPrompt.trim()}</span></div>
						{/if}
						<div class="flex items-baseline justify-between gap-3">
							<span class="shrink-0 text-text-muted">Behavior categories</span>
							<span class="min-w-0 break-words text-right font-medium text-text">{summaryTaxonomy}</span>
						</div>
						<div class="flex items-baseline justify-between gap-3">
							<span class="shrink-0 text-text-muted">Measurement suite</span>
							<span class="min-w-0 break-words text-right font-medium text-text">{suiteId.trim() || '(new)'}</span>
						</div>
						<div class="flex items-baseline justify-between gap-3">
							<span class="shrink-0 text-text-muted">Run</span>
							<span class="min-w-0 break-words text-right font-medium text-text">{runId.trim() || 'v1'}</span>
						</div>
						{#if step2Source === 'new'}
							<div class="flex items-baseline justify-between gap-3">
								<span class="shrink-0 text-text-muted">Query pipeline</span>
								<span class="min-w-0 break-words text-right font-medium text-text">{summaryTestCasesPipeline}</span>
							</div>
							{#if scenarioTestCasesEnabled || scenarioEvalEnabled}
								<div class="flex items-baseline justify-between gap-3">
									<span class="shrink-0 text-text-muted">Audit pipeline</span>
									<span class="min-w-0 break-words text-right font-medium text-text">{summaryScenarioPipeline}</span>
								</div>
							{/if}
						{/if}
					</div>
				</div>

				<div class="mb-5">
					<h3 class="mb-1 text-base font-semibold text-text">Measurement suite & run identity</h3>
					<p class="mb-3 text-xs text-text-muted">Measurement suites group policy + seeds; runs hold measurement results.</p>
					<div class="grid grid-cols-2 gap-4">
						<div>
							<label for="suite-id" class="mb-1 block text-xs font-semibold text-text-secondary">Measurement suite ID</label>
							<input id="suite-id" type="text" maxlength="150" class="form-control w-full text-sm" placeholder="Auto-generated if blank" value={suiteId} oninput={(e) => { suiteId = e.currentTarget.value; markDirty(); }} disabled={submitting} />
						</div>
						<div>
							<label for="run-id" class="mb-1 block text-xs font-semibold text-text-secondary">Run ID <span class="text-score-fail">*</span></label>
							<input id="run-id" type="text" maxlength="150" class="form-control w-full text-sm {!runId.trim() ? 'border-score-fail' : ''}" placeholder="e.g., v1" value={runId} oninput={(e) => { runId = e.currentTarget.value; markDirty(); }} disabled={submitting} />
							{#if !runId.trim()}<p class="mt-1 text-xs text-score-fail">Run ID is required.</p>{/if}
						</div>
					</div>
				</div>

				{#if submitError}
					<div class="mb-4 rounded-md border border-score-fail/30 bg-score-fail/5 p-3 text-sm text-score-fail">{submitError}</div>
				{/if}
			{/if}
		</div>

		<!-- Footer buttons -->
		<div class="mt-4 flex items-center justify-between">
			<button class="btn" onclick={handleCancel} disabled={submitting}>Cancel</button>
			<div class="flex items-center gap-2">
				{#if currentStep > 1}
					<button class="btn" onclick={handleBack} disabled={submitting}>Back</button>
				{/if}
				{#if currentStep < 3}
					<button class="btn btn-primary" disabled={!stepValid(currentStep)} onclick={handleContinue}>Continue</button>
				{:else}
					<button class="btn btn-primary" disabled={!step1Valid || !step2Valid || !step3Valid || submitting} onclick={handleSubmit}>
						{#if submitting}
							<svg class="-ml-0.5 mr-1.5 inline-block h-3.5 w-3.5 animate-spin" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/></svg>
							Submitting…
						{:else}
							Submit evaluation
						{/if}
					</button>
				{/if}
			</div>
		</div>
	</div>
</div>

<!-- Discard modal -->
{#if showDiscardModal}
	<div class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" role="dialog" aria-modal="true" aria-labelledby="discard-title">
		<div class="fixed inset-0" role="presentation" onclick={() => (showDiscardModal = false)}></div>
		<div class="relative z-10 w-full max-w-sm rounded-lg border border-border bg-surface p-5 shadow-xl">
			<h3 id="discard-title" class="text-base font-semibold text-text">Discard changes?</h3>
			<p class="mt-2 text-sm text-text-secondary">You have unsaved changes. Leaving will discard your setup.</p>
			<div class="mt-5 flex items-center justify-end gap-2">
				<button class="btn" onclick={() => (showDiscardModal = false)}>Stay</button>
				<button class="btn btn-danger" onclick={() => { isDirty = false; showDiscardModal = false; goto('/'); }}>Discard</button>
			</div>
		</div>
	</div>
{/if}

<!-- Add model modal -->
{#if addModelOpen}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
		role="dialog"
		tabindex="-1"
		aria-modal="true"
		aria-labelledby="add-model-title"
		onkeydown={(e) => { if (e.key === 'Escape') closeAddModel(); }}
	>
		<div class="fixed inset-0" role="presentation" onclick={closeAddModel}></div>
		<div class="relative z-10 w-full max-w-md rounded-lg border border-border bg-surface p-5 shadow-xl">
			<h3 id="add-model-title" class="text-base font-semibold text-text">Add model</h3>
			<p class="mt-1 text-xs text-text-muted">Specify a model id in <code class="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px] text-text-secondary">provider/model</code> form.</p>
			<div class="mt-4">
				<label for="add-model-input" class="mb-1 block text-xs font-semibold text-text-secondary">Model id</label>
				<input
					id="add-model-input"
					bind:this={addModelInput}
					type="text"
					autocomplete="off"
					placeholder="e.g., openai/gpt-4o-mini"
					class="form-control w-full text-sm {addModelError ? 'border-score-fail' : ''}"
					value={addModelValue}
					oninput={(e) => { addModelValue = e.currentTarget.value; addModelError = ''; }}
					onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); confirmAddModel(); } }}
				/>
				{#if addModelError}
					<p class="mt-1 text-xs text-score-fail">{addModelError}</p>
				{:else}
					<p class="mt-1 text-xs text-text-muted">Examples: <code class="font-mono">openai/gpt-4o-mini</code>, <code class="font-mono">azure/my-deployment</code>, <code class="font-mono">anthropic/claude-3.5-sonnet</code>.</p>
				{/if}
			</div>
			<div class="mt-5 flex items-center justify-end gap-2">
				<button class="btn" onclick={closeAddModel}>Cancel</button>
				<button class="btn btn-primary" onclick={confirmAddModel} disabled={!addModelValue.trim()}>Add model</button>
			</div>
		</div>
	</div>
{/if}
