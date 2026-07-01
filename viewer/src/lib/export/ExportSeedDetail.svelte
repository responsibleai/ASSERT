<!--
  Copyright (c) Microsoft Corporation.
  Licensed under the MIT License.
-->
<script lang="ts">
	import type {
		AuditCitation,
		InteractionMessage,
		MultiJudge,
		NodeJudgment,
		SeedTool,
		Verdict,
		ViewerResultItem
	} from '$lib/types.js';
	import { renderMarkdown } from '$lib/markdown.js';
	import {
		getJudgeError,
		getVerdictFlag,
		multiJudgeDimensionAgreementLabel
	} from '$lib/judgment.js';

	let {
		item,
		metricNames,
		primaryMetric
	}: {
		item: ViewerResultItem;
		metricNames: string[];
		primaryMetric: string;
	} = $props();

	const verdict = $derived((item.verdict as Verdict | null | undefined) ?? null);
	const judgeError = $derived(getJudgeError(item));
	const isJudgeFailed = $derived(item.judge_status === 'judge_failed');
	const multiJudge = $derived(item.multi_judge as MultiJudge | undefined);

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
	function metricRateText(rate: number | null): string {
		return rate == null ? 'N/A' : `${(rate * 100).toFixed(0)}%`;
	}
	function metricDotColor(flag: boolean): string {
		return flag ? 'var(--color-score-fail)' : 'var(--color-score-pass)';
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
	function policyNodeName(node: NodeJudgment): string | null {
		const name = typeof node.node_name === 'string' ? node.node_name.trim() : '';
		return name || null;
	}
	function visibleNodeJudgments(nodes: NodeJudgment[]): NodeJudgment[] {
		const relevant = nodes.filter((n) => n.relevant);
		return relevant.length > 0 ? relevant : nodes;
	}
	function conversationTurnCount(messages: InteractionMessage[]): number {
		return messages.filter((m) => m.role !== 'system').length;
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
	const judgeViews = $derived(buildJudgeViews());
	const hasMultipleJudges = $derived((multiJudge?.verdicts?.length ?? 0) > 1);

	const verdictCitations: AuditCitation[] = $derived(
		Array.isArray(verdict?.citations) ? (verdict?.citations as AuditCitation[]) : []
	);
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
								style={vote === null ? 'background: transparent; box-shadow: inset 0 0 0 1.5px var(--color-text-muted);' : `background: ${metricDotColor(vote)}`}
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
							<div class="mb-1 text-[10px] font-semibold uppercase tracking-wider text-text-muted">Conversation summary</div>
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
															<div class="text-xs font-semibold text-text" title={policyNodeName(node) ?? 'Unnamed policy node'}>
																{policyNodeName(node) ?? 'Unnamed policy node'}
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

	<!-- Right column: conversation -->
	<div class="lg:col-span-3 space-y-3">
		<h3 class="text-xs font-semibold uppercase tracking-widest text-text-muted">
			Conversation · {conversationTurnCount(item.messages)} turns
		</h3>
		{#each item.messages as message}
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
	</div>
</div>
