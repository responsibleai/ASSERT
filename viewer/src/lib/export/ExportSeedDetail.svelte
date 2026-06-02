<script lang="ts">
	import type {
		AuditCitation,
		InteractionMessage,
		MultiJudge,
		NodeJudgment,
		SeedTool,
		TrajectoryStep,
		Verdict,
		ViewerResultItem
	} from '$lib/types.js';
	import { renderMarkdown } from '$lib/markdown.js';
	import {
		getJudgeError,
		getRecordFlag,
		getVerdictFlag,
		multiJudgeDimensionAgreementLabel
	} from '$lib/judgment.js';
	import { judgeDimensionLabel } from '$lib/labels.js';

	let {
		item,
		metricNames,
		primaryMetric
	}: {
		item: ViewerResultItem;
		metricNames: string[];
		primaryMetric: string;
	} = $props();

	let verdict = $derived((item.verdict as Verdict | null | undefined) ?? null);
	let judgeError = $derived(getJudgeError(item));
	let isJudgeFailed = $derived(item.judge_status === 'judge_failed');
	let multiJudge = $derived(item.multi_judge as MultiJudge | undefined);

	function metricLabel(metric: string): string {
		return judgeDimensionLabel(metric);
	}
	function metricOutcomeText(flag: boolean | null): string {
		if (flag === null) return 'n/a';
		return flag ? 'flagged' : 'clear';
	}
	function metricOutcomeClass(flag: boolean | null): string {
		if (flag === null) return 'text-text-muted';
		return flag ? 'text-score-fail' : 'text-score-pass';
	}
	function metricRateText(rate: number): string {
		return `${(rate * 100).toFixed(0)}%`;
	}
	function metricDotColor(flag: boolean): string {
		return flag ? 'var(--theme-score-fail)' : 'var(--theme-score-pass)';
	}
	function getDimensionJustification(v: Verdict | null | undefined, metric: string): string | null {
		if (!v || typeof v !== 'object') return null;
		const dj = v.dimension_justifications;
		if (dj && typeof dj === 'object') {
			const value = dj[metric];
			if (typeof value === 'string' && value.trim()) return value;
		}
		return null;
	}
	function behaviorCategoryName(node: NodeJudgment): string | null {
		const name = typeof node.node_name === 'string' ? node.node_name.trim() : '';
		return name || null;
	}
	function visibleNodeJudgments(nodes: NodeJudgment[]): NodeJudgment[] {
		const relevant = nodes.filter((n) => n.relevant);
		return relevant.length > 0 ? relevant : nodes;
	}
	function resultTurnCount(messages: InteractionMessage[]): number {
		return messages.filter((m) => m.role !== 'system').length;
	}
	function trajectoryStepBadgeLabel(step: TrajectoryStep): string {
		const t = step.type ?? '';
		if (t === 'tool_call') return 'CALL';
		if (t === 'tool_result') return 'RESULT';
		if (t === 'llm_call') return 'LLM';
		if (t === 'command') return 'CMD';
		if (t === 'guardrail') return 'GUARD';
		if (t === 'error') return 'ERROR';
		if (t === 'system') return 'SYSTEM';
		if (t === 'reasoning') return 'REASON';
		return t.toUpperCase().slice(0, 8) || 'STEP';
	}
	function trajectoryStepCardClass(step: TrajectoryStep): string {
		const t = step.type ?? '';
		if (t === 'tool_call') return 'border-blue-500/25 border-l-4 border-l-blue-400 bg-blue-500/5';
		if (t === 'tool_result') return 'border-emerald-500/25 border-l-4 border-l-emerald-400 bg-emerald-500/5';
		if (t === 'llm_call') return 'border-sky-500/25 border-l-4 border-l-sky-400 bg-sky-500/5';
		if (t === 'command') return 'border-orange-500/25 border-l-4 border-l-orange-400 bg-orange-500/5';
		if (t === 'guardrail') return 'border-amber-500/25 border-l-4 border-l-amber-400 bg-amber-500/5';
		if (t === 'error') return 'border-score-fail/30 border-l-4 border-l-score-fail bg-score-fail/5';
		if (t === 'system') return 'border-yellow-500/25 border-l-4 border-l-yellow-400 bg-yellow-500/5';
		if (t === 'reasoning') return 'border-violet-500/25 border-l-4 border-l-violet-400 bg-violet-500/5';
		return 'border-border bg-surface/30';
	}
	function trajectoryStepBadgeClass(step: TrajectoryStep): string {
		const t = step.type ?? '';
		if (t === 'tool_call') return 'border-blue-400/30 bg-blue-500/15 text-blue-300';
		if (t === 'tool_result') return 'border-emerald-400/30 bg-emerald-500/15 text-emerald-300';
		if (t === 'llm_call') return 'border-sky-400/30 bg-sky-500/15 text-sky-300';
		if (t === 'command') return 'border-orange-400/30 bg-orange-500/15 text-orange-300';
		if (t === 'guardrail') return 'border-amber-400/30 bg-amber-500/15 text-amber-300';
		if (t === 'error') return 'border-score-fail/35 bg-score-fail/15 text-score-fail';
		if (t === 'system') return 'border-yellow-400/30 bg-yellow-500/15 text-yellow-300';
		if (t === 'reasoning') return 'border-violet-400/30 bg-violet-500/15 text-violet-300';
		return 'border-border bg-surface text-text-muted';
	}
	function trajectoryStepTitle(step: TrajectoryStep): string {
		if (step.name) return step.name;
		if (step.role) return `${step.role}`;
		return step.type ?? 'step';
	}
	function trajectoryStepBody(step: TrajectoryStep): string | null {
		if (typeof step.content === 'string' && step.content.trim()) return step.content;
		if (typeof step.result_preview === 'string' && step.result_preview.trim()) return step.result_preview;
		if (step.result !== undefined && step.result !== null) {
			try {
				return typeof step.result === 'string' ? step.result : JSON.stringify(step.result, null, 2);
			} catch {
				return null;
			}
		}
		if (step.args && Object.keys(step.args).length > 0) {
			try {
				return JSON.stringify(step.args, null, 2);
			} catch {
				return null;
			}
		}
		return null;
	}

	type JudgeView = {
		label: string;
		v: Verdict | null;
	};

	function buildJudgeViews(): JudgeView[] {
		const views: JudgeView[] = [{ label: 'Aggregate', v: verdict }];
		if (multiJudge?.verdicts) {
			multiJudge.verdicts.forEach((v, i) => {
				views.push({ label: `Judge ${i + 1}`, v: v });
			});
		}
		return views;
	}
	let judgeViews = $derived(buildJudgeViews());
	let hasMultipleJudges = $derived((multiJudge?.verdicts?.length ?? 0) > 1);

	let verdictCitations: AuditCitation[] = $derived(Array.isArray(verdict?.citations)
		? (verdict?.citations as AuditCitation[])
		: []);
</script>

<div class="grid gap-5 lg:grid-cols-5">
	<!-- Left column: scenario context + judgment -->
	<div class="lg:col-span-2 space-y-3">
		{#if item.kind === 'scenario' && item.context.description}
			<div class="rounded-lg border border-border/50 bg-surface/50">
				<div class="flex w-full items-center gap-2 px-4 py-3 text-left">
					<h3 class="text-xs font-semibold uppercase tracking-widest text-text-muted">Scenario</h3>
					<span class="flex-1 truncate text-sm font-medium text-text">{item.header_title}</span>
				</div>
				<div class="border-t border-border/30 px-4 pb-4 pt-3">
					<p class="whitespace-pre-wrap text-sm text-text-secondary leading-relaxed">{item.context.description}</p>
					{#if item.context.tools?.length}
						<div class="mt-3 border-t border-border/30 pt-3">
							<details>
								<summary class="flex cursor-pointer items-center gap-1.5 list-none">
									<svg class="h-3 w-3 text-text-muted/60 transition-transform" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
									<h4 class="text-xs font-semibold uppercase tracking-widest text-text-muted">Tools</h4>
									<span class="text-[10px] text-text-muted">{item.context.tools.map((t: SeedTool) => t.name).join(', ')}</span>
								</summary>
								<div class="mt-2 space-y-2">
									{#each item.context.tools as tool}
										<div class="rounded border border-border/30 bg-bg/50 px-3 py-2">
											<span class="font-mono text-xs font-semibold text-purple-300">{tool.name}</span>
											{#if tool.description}
												<p class="mt-0.5 text-xs text-text-secondary">{tool.description}</p>
											{/if}
											{#if tool.parameters?.length}
												<div class="mt-1.5 flex flex-wrap gap-1">
													{#each tool.parameters as parameter}
														<span class="inline-flex items-center rounded border border-border/20 bg-surface px-1.5 py-0.5 font-mono text-[10px] text-text-muted">
															{parameter.name}<span class="ml-0.5 text-text-muted/50">:{parameter.type || 'string'}</span>
														</span>
													{/each}
												</div>
											{/if}
										</div>
									{/each}
								</div>
							</details>
						</div>
					{/if}
				</div>
			</div>
		{/if}

		<h3 class="text-xs font-semibold uppercase tracking-widest text-text-muted">Judgment</h3>

		{#if isJudgeFailed}
			<div class="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
				<div class="text-xs font-semibold uppercase tracking-wider text-amber-400">Judge failed</div>
				<p class="mt-2 text-sm text-text-secondary leading-relaxed">
					{judgeError ?? 'The judge did not produce a valid scored verdict for this result.'}
				</p>
			</div>
		{/if}

		{#each judgeViews as judgeView, jvIdx}
			{@const v = judgeView.v}
			{#if v}
				<div class="rounded-lg border border-border bg-surface">
					{#if hasMultipleJudges}
						{@const vote = getVerdictFlag(v, primaryMetric)}
						<div class="flex items-center gap-1.5 border-b border-border/50 px-3 py-2">
							<span
								class="inline-block size-[6px] rounded-full"
								style={vote === null ? 'background: transparent; box-shadow: inset 0 0 0 1.5px var(--theme-text-muted);' : `background: ${metricDotColor(vote)}`}
							></span>
							<span class="text-xs font-semibold text-text-secondary">{judgeView.label}</span>
							<span class="text-[10px] text-text-muted">· {metricOutcomeText(vote)}</span>
							{#if jvIdx === 0 && multiJudge}
								<span class="ml-auto text-[10px] text-text-muted">representative judge {typeof multiJudge.representative_index === 'number' ? multiJudge.representative_index + 1 : '—'}</span>
							{/if}
						</div>
					{/if}

					{#if v.narrative}
						<div class="border-b border-border/30 px-4 py-3">
							<div class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">Result summary</div>
							<p class="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">{v.narrative}</p>
						</div>
					{/if}

					<div class="space-y-3 p-4">
						{#each metricNames as m}
							{@const flag = getVerdictFlag(v, m)}
							{@const dj = getDimensionJustification(v, m)}
							{#if flag !== null}
								<div class="rounded-md bg-bg/40 p-3">
									<div class="mb-2 flex items-center justify-between gap-2">
										<span class="text-xs font-semibold uppercase tracking-wider text-text-muted">{metricLabel(m)}</span>
										<div class="flex items-center gap-2">
											{#if jvIdx === 0 && multiJudge?.votes?.[m]}
												<div class="flex items-center gap-0.5">
													{#each multiJudge.votes[m] as vote}
														{@const agreed = vote === flag}
														<span class="inline-block size-[7px] rounded-full" style={agreed ? `background: ${metricDotColor(vote)}` : `background: transparent; box-shadow: inset 0 0 0 1.5px ${metricDotColor(vote)}`}></span>
													{/each}
												</div>
												<span class="text-[10px] tabular-nums text-text-muted">{multiJudgeDimensionAgreementLabel(multiJudge, m)}</span>
												<span class="text-[10px] tabular-nums text-text-muted">flagged {metricRateText(multiJudge.means?.[m] ?? 0)}</span>
											{/if}
											<span class="text-base font-bold tabular-nums {metricOutcomeClass(flag)}">{metricOutcomeText(flag)}</span>
										</div>
									</div>
									{#if dj}
										<div class="prose max-w-none text-sm text-text-secondary leading-relaxed">{@html renderMarkdown(dj)}</div>
									{:else if m === primaryMetric && typeof v.justification === 'string'}
										<div class="prose max-w-none text-sm text-text-secondary leading-relaxed">{@html renderMarkdown(v.justification)}</div>
									{/if}

									{#if m === 'policy_violation' && v.node_judgments?.length > 0}
										<div class="mt-3 space-y-1.5 border-t border-border/40 pt-3">
											{#each visibleNodeJudgments(v.node_judgments) as node}
												{@const violated = node.violated}
												<div class="rounded-md px-3 py-2 {violated ? 'bg-score-fail/5' : violated === null ? 'bg-surface-2/50' : 'bg-score-pass/5'}">
													<div class="flex items-start justify-between gap-2">
														<div class="min-w-0 flex-1">
															<div class="text-xs font-semibold text-text" title={behaviorCategoryName(node) ?? 'Unnamed behavior category'}>
																{behaviorCategoryName(node) ?? 'Unnamed behavior category'}
															</div>
														</div>
														<div class="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
															<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium {violated ? 'bg-score-fail/10 text-score-fail' : violated === null ? 'bg-surface-2 text-text-muted' : 'bg-score-pass/10 text-score-pass'}">
																{violated === null ? 'not assessed' : violated ? 'violated' : 'clear'}
															</span>
															{#if node.confidence}
																<span class="text-[10px] text-text-muted">{node.confidence} confidence</span>
															{/if}
														</div>
													</div>
													{#if node.reasoning}
														<div class="prose mt-1.5 max-w-none text-sm text-text-secondary leading-relaxed">{@html renderMarkdown(node.reasoning)}</div>
													{/if}
												</div>
											{/each}
										</div>
									{/if}
								</div>
							{/if}
						{/each}
					</div>

					{#if jvIdx === 0 && verdictCitations.length > 0}
						<details class="border-t border-border/30 px-4 py-3">
							<summary class="cursor-pointer text-[11px] font-semibold uppercase tracking-wider text-text-muted">Citations ({verdictCitations.length})</summary>
							<ol class="mt-2 list-decimal space-y-1.5 pl-5 text-xs text-text-secondary">
								{#each verdictCitations as citation}
									<li>
										<span class="font-mono text-text-muted">[{citation.index}]</span>
										{#if citation.parts?.length}
											{#each citation.parts as part, pi}
												{#if pi > 0}, {/if}
												<span class="font-mono text-[10px] text-text-muted">msg={part.message_id ?? '?'}</span>
											{/each}
										{/if}
										{#if citation.description}
											<div class="mt-0.5 text-text-muted">{citation.description}</div>
										{/if}
									</li>
								{/each}
							</ol>
						</details>
					{/if}
				</div>
			{/if}
		{/each}
	</div>

	<!-- Right column: result transcript -->
	<div class="lg:col-span-3 space-y-3">
		<h3 class="text-xs font-semibold uppercase tracking-widest text-text-muted">
			Result · {resultTurnCount(item.messages)} turns
		</h3>
		{#each item.messages as message, mi}
			{@const isSystem = message.role === 'system'}
			{@const isUser = message.role === 'user'}
			{@const isToolResult = message.role === 'tool' || message.type === 'tool_call'}
			{@const isAssistantToolCall = message.role === 'assistant' && (message.tool_calls?.length ?? 0) > 0}
			{#if isSystem}
				<details class="rounded-lg border border-yellow-500/20 bg-yellow-500/8 overflow-hidden">
					<summary class="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5">
						<svg class="h-3 w-3 text-yellow-400/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
						<span class="text-xs font-semibold text-yellow-400">{message.type === 'set_system_message' ? 'System Prompt Set' : 'System Prompt'}</span>
					</summary>
					<div class="border-t border-yellow-500/10 px-4 pb-3 pt-2">
						<div class="prose max-w-none text-sm text-text-secondary leading-relaxed">{@html renderMarkdown(message.content)}</div>
					</div>
				</details>
			{:else if isAssistantToolCall}
				<div class="space-y-2">
					{#if message.content?.trim()}
						<div class="rounded-lg border border-border bg-surface px-4 py-2.5">
							<div class="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Assistant</div>
							<div class="prose mt-1 max-w-none text-sm text-text leading-relaxed">{@html renderMarkdown(message.content)}</div>
						</div>
					{/if}
					{#each message.tool_calls ?? [] as tc}
						<details open class="rounded-lg border border-purple-500/20 bg-purple-500/8 overflow-hidden">
							<summary class="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5">
								<svg class="h-3 w-3 text-purple-400/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
								<span class="text-xs font-semibold text-purple-400">Tool Call</span>
								<span class="font-mono text-xs text-text-muted">{tc.function}</span>
							</summary>
							<div class="border-t border-purple-500/10 px-4 pb-3 pt-2">
								<pre class="overflow-x-auto rounded bg-bg/50 p-2 text-xs text-text-secondary">{JSON.stringify(tc.arguments, null, 2)}</pre>
							</div>
						</details>
					{/each}
				</div>
			{:else if isToolResult}
				<details open class="rounded-lg border border-purple-500/15 bg-purple-500/5 overflow-hidden">
					<summary class="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5">
						<svg class="h-3 w-3 text-purple-400/60" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
						<span class="text-xs font-semibold text-purple-400">Tool Result{message.judgeTurn != null ? ` · Turn ${message.judgeTurn}` : ''}</span>
						{#if message.function}
							<span class="truncate font-mono text-xs text-text-muted">{message.function}</span>
						{/if}
					</summary>
					<div class="border-t border-purple-500/10 px-4 pb-3 pt-2">
						<div class="prose max-w-none text-sm text-text-secondary leading-relaxed">{@html renderMarkdown(message.content)}</div>
					</div>
				</details>
			{:else if isUser}
				<div class="rounded-lg border border-border bg-surface-2/40 px-4 py-2.5">
					<div class="text-[10px] font-semibold uppercase tracking-wider text-text-muted">User{message.judgeTurn != null ? ` · Turn ${message.judgeTurn}` : ''}</div>
					<div class="prose mt-1 max-w-none text-sm text-text leading-relaxed">{@html renderMarkdown(message.content)}</div>
				</div>
			{:else}
				<div class="rounded-lg border border-border bg-surface px-4 py-2.5">
					<div class="text-[10px] font-semibold uppercase tracking-wider text-text-muted">Assistant{message.judgeTurn != null ? ` · Turn ${message.judgeTurn}` : ''}</div>
					<div class="prose mt-1 max-w-none text-sm text-text leading-relaxed">{@html renderMarkdown(message.content)}</div>
				</div>
			{/if}
		{/each}

		{#if item.trajectory && item.trajectory.steps?.length}
			<details class="rounded-lg border border-border bg-surface/40">
				<summary class="cursor-pointer list-none px-4 py-2.5">
					<div class="flex items-center gap-2">
						<svg class="h-3 w-3 text-text-muted/70" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
						<span class="text-xs font-semibold uppercase tracking-widest text-text-muted">Agent trace ({item.trajectory.steps.length} steps)</span>
						{#if item.trajectory.stop_reason}
							<span class="ml-auto text-[10px] text-text-muted">stop: {item.trajectory.stop_reason}</span>
						{/if}
					</div>
				</summary>
				<div class="space-y-2 border-t border-border/40 px-4 py-3">
					{#each item.trajectory.steps as step, si}
						{@const body = trajectoryStepBody(step)}
						<div class="rounded-lg border {trajectoryStepCardClass(step)}">
							<div class="flex flex-wrap items-center gap-2 px-3 py-2">
								<span class="font-mono text-[10px] text-text-muted">{step.step_id ?? `#${si}`}</span>
								<span class="inline-flex items-center rounded border px-1.5 py-0.5 text-[9px] font-bold tracking-wide {trajectoryStepBadgeClass(step)}">{trajectoryStepBadgeLabel(step)}</span>
								<span class="text-xs font-semibold text-text">{trajectoryStepTitle(step)}</span>
								{#if step.status}
									<span class="rounded bg-bg/60 px-1.5 py-0.5 text-[10px] text-text-muted">{step.status}</span>
								{/if}
							</div>
							{#if body}
								<pre class="max-h-44 overflow-y-auto whitespace-pre-wrap break-words border-t border-border/40 px-3 py-2 text-xs text-text-secondary">{body}</pre>
							{/if}
						</div>
					{/each}
				</div>
			</details>
		{/if}
	</div>
</div>
