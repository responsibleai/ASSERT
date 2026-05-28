<!-- Copyright (c) Microsoft Corporation.
     Licensed under the MIT License. -->

<script lang="ts">
	import {
		getJudgeError,
		getRecordFlag,
		getVerdictFlag,
		inferJudgeStatus
	} from '$lib/judgment.js';
	import {
		getCitationDisplayRanges,
		parseCitationReferences
	} from '$lib/citation-resolution';
	import { renderMarkdown, renderMarkdownWithHighlights } from '$lib/markdown';
	import { formatFactorLabel } from '$lib/grouping.js';
	import type {
		AuditCitation,
		AuditCitationPart,
		AuditCitationSourceKind,
		InteractionMessage,
		LlmCallTrace,
		NodeJudgment,
		SeedTool,
		StopReasonDisplay,
		Verdict,
		ViewerResultItem
	} from '$lib/types.js';

	let {
		item,
		metricNames,
		primaryMetric,
		requiredBaseMetrics,
		navIdx,
		navTotal,
		onClose,
		onPrev,
		onNext
	}: {
		item: ViewerResultItem;
		metricNames: string[];
		primaryMetric: string;
		requiredBaseMetrics: string[];
		navIdx: number;
		navTotal: number;
		onClose: () => void;
		onPrev: () => void;
		onNext: () => void;
	} = $props();

	interface ToolFocusState {
		messageId: string;
		sourceKind: AuditCitationSourceKind;
		toolCallId?: string | null;
		toolArg?: string | null;
	}

	interface MessageDebugView {
		label: string;
		buttonTitle: string;
		panelTitle: string;
		description: string;
		payload: Record<string, unknown>;
	}

	interface RawPanelView {
		request?: string;
		response?: string;
		payload?: string;
	}

	let highlightedTurn = $state<number | null>(null);
	let highlightedMessageId = $state<string | null>(null);
	let highlightedToolFocus = $state<ToolFocusState | null>(null);
	let highlightResetHandle: ReturnType<typeof setTimeout> | null = null;
	let activeJudgeIndex = $state(0);

	$effect(() => {
		const handler = (event: KeyboardEvent) => {
			if (event.defaultPrevented) return;
			const target = event.target as HTMLElement | null;
			if (target) {
				const tag = target.tagName;
				if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable) return;
			}
			if (event.key === 'Escape') {
				event.preventDefault();
				onClose();
			} else if (event.key === 'ArrowLeft') {
				event.preventDefault();
				onPrev();
			} else if (event.key === 'ArrowRight') {
				event.preventDefault();
				onNext();
			}
		};
		window.addEventListener('keydown', handler);
		return () => window.removeEventListener('keydown', handler);
	});
	let displayedCitations = $state<AuditCitation[] | null>(null);
	let contextExpanded = $state(false);
	let expandedMessageBodies = $state<Record<string, boolean>>({});
	let rawPanels = $state<Record<string, RawPanelView>>({});

	let citationTooltipVisible = $state(false);
	let citationTooltipX = $state(0);
	let citationTooltipY = $state(0);
	let citationTooltipCitations = $state<AuditCitation[]>([]);
	let citationTooltipHideHandle: ReturnType<typeof setTimeout> | null = null;

	$effect(() => {
		item.id;
		highlightedTurn = null;
		highlightedMessageId = null;
		highlightedToolFocus = null;
		activeJudgeIndex = 0;
		displayedCitations = null;
		contextExpanded = false;
		expandedMessageBodies = {};
		rawPanels = {};
		citationTooltipVisible = false;
		citationTooltipCitations = [];
		if (highlightResetHandle) {
			clearTimeout(highlightResetHandle);
			highlightResetHandle = null;
		}
		if (citationTooltipHideHandle) {
			clearTimeout(citationTooltipHideHandle);
			citationTooltipHideHandle = null;
		}
	});

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
		const spaced = metric.replace(/_/g, ' ');
		return spaced.charAt(0).toUpperCase() + spaced.slice(1);
	}

	function metricOutcomeText(flag: boolean | null): string {
		if (flag === null) return 'N/A';
		return flag ? 'Flagged' : 'Pass';
	}

	function metricOutcomeClass(flag: boolean | null): string {
		if (flag === null) return 'text-text-muted';
		return flag ? 'text-score-fail' : 'text-score-pass';
	}

	function metricDotColor(flag: boolean): string {
		return flag ? 'var(--theme-score-fail)' : 'var(--theme-score-pass)';
	}

	function metricRateText(rate: number): string {
		return `${(rate * 100).toFixed(0)}%`;
	}

	function judgmentWarningLabel(warning: string): string {
		if (warning === 'policy_violation_without_violated_node') {
			return 'top-level policy violation is flagged, but no policy node is marked violated';
		}
		if (warning === 'violated_node_without_policy_violation') {
			return 'a policy node is marked violated, but the top-level policy verdict is clear';
		}
		return warning.replace(/_/g, ' ');
	}

	function runtimeModeLabel(runtimeMode?: string | null, hasAgenticTranscript = false): string | null {
		if (runtimeMode === 'chat') return 'chat';
		if (runtimeMode === 'tool_module') return 'tools';
		if (runtimeMode === 'simulated') return 'simulated tools';
		if (runtimeMode === 'external') return 'external tools';
		return hasAgenticTranscript ? 'agentic transcript' : null;
	}

	function isStructuredCitation(value: unknown): value is AuditCitation {
		return Boolean(value) && typeof value === 'object' && Array.isArray((value as AuditCitation).parts);
	}

	function getStructuredCitations(citations: unknown): AuditCitation[] {
		return Array.isArray(citations) ? citations.filter(isStructuredCitation) : [];
	}

	function stopReasonLabel(stopReason: string | null | undefined, display?: StopReasonDisplay | null): string {
		return display?.label ?? stopReason ?? '';
	}

	function stopReasonTitle(stopReason: string | null | undefined, display?: StopReasonDisplay | null): string {
		if (!stopReason) return display?.description ?? '';
		if (!display) return stopReason;
		return `${display.description} Stop reason: ${stopReason}`;
	}

	function stopReasonChipClass(display?: StopReasonDisplay | null): string {
		if (display) {
			return 'rounded bg-amber-500/10 px-1.5 py-0.5 text-[10px] font-medium text-amber-400';
		}
		return 'rounded bg-surface-2 px-1.5 py-0.5 text-[10px] text-text-muted';
	}

	function getDimensionJustification(verdict: Verdict | null | undefined, metric: string): string | null {
		if (!verdict || typeof verdict !== 'object') return null;
		const dimensionJustifications = verdict.dimension_justifications;
		if (dimensionJustifications && typeof dimensionJustifications === 'object') {
			const value = dimensionJustifications[metric];
			if (typeof value === 'string' && value.trim()) return value;
		}
		return null;
	}

	function getActiveVerdictCitations(
		selectedVerdict: Verdict | null | undefined,
		fallbackCitations: AuditCitation[]
	): AuditCitation[] {
		const verdictCitations = getStructuredCitations(selectedVerdict?.citations);
		return verdictCitations.length > 0 ? verdictCitations : fallbackCitations;
	}

	function citationPartsForMessage(citations: AuditCitation[], messageId?: string | null): AuditCitationPart[] {
		if (!messageId) return [];
		return citations.flatMap((citation) =>
			(citation.parts ?? []).filter((part) => part.message_id === messageId)
		);
	}

	function renderAuditMessageHtml(content: string, parts: AuditCitationPart[]): string {
		if (!parts.length) return renderMarkdown(content);
		const ranges = getCitationDisplayRanges(content, parts);
		return renderMarkdownWithHighlights(content, ranges);
	}

	function renderMessageHtml(message: InteractionMessage, parts: AuditCitationPart[]): string {
		return parts.length > 0
			? renderAuditMessageHtml(message.content, parts)
			: renderMarkdown(message.content);
	}

	function escapeInlineHtml(text: string): string {
		return text
			.replaceAll('&', '&amp;')
			.replaceAll('<', '&lt;')
			.replaceAll('>', '&gt;')
			.replaceAll('"', '&quot;')
			.replaceAll("'", '&#39;');
	}

	function clearCitationTooltipHideTimer() {
		if (!citationTooltipHideHandle) return;
		clearTimeout(citationTooltipHideHandle);
		citationTooltipHideHandle = null;
	}

	function hideCitationTooltip() {
		citationTooltipVisible = false;
		citationTooltipCitations = [];
		clearCitationTooltipHideTimer();
	}

	function scheduleCitationTooltipHide() {
		clearCitationTooltipHideTimer();
		citationTooltipHideHandle = setTimeout(() => {
			hideCitationTooltip();
		}, 120);
	}

	function parseCitationIndices(raw: string | undefined): number[] {
		if (!raw) return [];
		return raw
			.split(',')
			.map((value) => Number.parseInt(value.trim(), 10))
			.filter((value) => Number.isInteger(value) && value > 0);
	}

	function getCitationButtonHtml(indices: number[], label: string): string {
		const indicesText = indices.join(', ');
		const escapedLabel = escapeInlineHtml(label);
		return `<button type="button" class="citation-ref" data-citation-indices="${indices.join(',')}" title="Jump to citation ${indicesText}" aria-label="Jump to citation ${indicesText}">${escapedLabel}</button>`;
	}

	function renderTextWithCitationButtons(text: string): string {
		const references = parseCitationReferences(text);
		if (references.length === 0) return renderMarkdown(text);

		let withTokens = '';
		let cursor = 0;
		const replacementEntries: Array<[string, string]> = [];
		for (let index = 0; index < references.length; index += 1) {
			const ref = references[index];
			withTokens += text.slice(cursor, ref.startPos);
			const token = `@@P2M_CIT_REF_${index}@@`;
			replacementEntries.push([token, getCitationButtonHtml(ref.indices, ref.originalText)]);
			withTokens += token;
			cursor = ref.endPos;
		}
		withTokens += text.slice(cursor);

		let html = renderMarkdown(withTokens);
		for (const [token, replacement] of replacementEntries) {
			html = html.replaceAll(token, replacement);
		}
		return html;
	}

	function applyMessageHighlight(turn: number | null, messageId?: string | null, toolFocus?: ToolFocusState | null) {
		highlightedTurn = turn;
		highlightedMessageId = messageId ?? null;
		highlightedToolFocus = toolFocus ?? null;
		if (highlightResetHandle) clearTimeout(highlightResetHandle);
		highlightResetHandle = setTimeout(() => {
			highlightedTurn = null;
			highlightedMessageId = null;
			highlightedToolFocus = null;
		}, 2200);
	}

	function messageAnchorId(messageId: string | null | undefined): string | undefined {
		return messageId ? `drawer-message-${messageId}` : undefined;
	}

	function messagePanelKey(
		message: InteractionMessage,
		messageIndex: number,
		part: string
	): string {
		return `${message.id ?? `message-${messageIndex}`}:${part}`;
	}

	function hasExpandedBody(key: string): boolean {
		return Boolean(expandedMessageBodies[key]);
	}

	function toggleMessageBody(key: string) {
		expandedMessageBodies = {
			...expandedMessageBodies,
			[key]: !expandedMessageBodies[key]
		};
	}

	function expandMessageBody(key: string | undefined) {
		if (!key || expandedMessageBodies[key]) return;
		expandedMessageBodies = {
			...expandedMessageBodies,
			[key]: true
		};
	}

	function toolCallAnchorId(messageId: string | null | undefined, toolCallId: string | null | undefined): string | undefined {
		return messageId && toolCallId ? `drawer-tool-call-${messageId}-${toolCallId}` : undefined;
	}

	function fallbackTurnLabel(messages: InteractionMessage[], messageIndex: number): number {
		// Mirrors the materializer: only auditor (user) and target (assistant)
		// emit turns. Tool calls and tool messages inherit the surrounding
		// assistant turn — they don't get their own number, but they DO show
		// the assistant's turn label so the viewer can group them under it.
		let count = 0;
		let lastPrincipalRole: 'user' | 'assistant' | null = null;
		for (let i = 0; i <= messageIndex; i += 1) {
			const message = messages[i];
			if (message.role === 'system') continue;
			if (message.role === 'user') {
				count += 1;
				lastPrincipalRole = 'user';
			} else {
				// assistant, tool, or tool_call: same turn as the surrounding assistant block.
				if (lastPrincipalRole !== 'assistant') count += 1;
				lastPrincipalRole = 'assistant';
			}
		}
		return count;
	}

	function revealMessageCard(el: HTMLElement) {
		expandMessageBody(el.dataset.messageBodyKey);
	}

	function findToolCallAnchorElement(messageId?: string | null, toolCallId?: string | null): HTMLElement | null {
		const anchor = toolCallAnchorId(messageId, toolCallId);
		if (!anchor) return null;
		return (
			Array.from(document.querySelectorAll<HTMLElement>('[data-tool-call-anchor]')).find(
				(el) => el.dataset.toolCallAnchor === anchor
			) ?? null
		);
	}

	function scrollToTurn(turn: number, messageId?: string | null, toolFocus?: ToolFocusState | null) {
		applyMessageHighlight(turn, messageId, toolFocus);
		const el =
			findToolCallAnchorElement(messageId, toolFocus?.toolCallId) ??
			document.getElementById(`drawer-turn-${turn}`);
		if (!el) return;
		revealMessageCard(el as HTMLElement);
		el.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}

	function readDebugObject(value: unknown): Record<string, unknown> | null {
		return value && typeof value === 'object' && !Array.isArray(value)
			? (value as Record<string, unknown>)
			: null;
	}

	function getMessageLlmCall(
		message: InteractionMessage,
		{ toolCallIndex = 0 }: { toolCallIndex?: number } = {}
	): LlmCallTrace | null {
		if (toolCallIndex > 0 && message.role === 'assistant' && (message.tool_calls?.length ?? 0) > 0) {
			return null;
		}
		if (!message.id) return null;
		return llmCallByMessageId.get(message.id) ?? null;
	}

	function isRawPanelOpen(key: string): boolean {
		return key in rawPanels;
	}

	function toggleRawPanel(
		key: string,
		{
			request,
			response,
			payload
		}: {
			request?: unknown;
			response?: unknown;
			payload?: Record<string, unknown>;
		}
	) {
		if (rawPanels[key]) {
			const nextPanels = { ...rawPanels };
			delete nextPanels[key];
			rawPanels = nextPanels;
			return;
		}

		rawPanels = {
			...rawPanels,
			[key]: {
				request: request === undefined ? undefined : JSON.stringify(request, null, 2),
				response: response === undefined ? undefined : JSON.stringify(response, null, 2),
				payload: payload === undefined ? undefined : JSON.stringify(payload, null, 2)
			}
		};
	}

	function getMessageDebugView(
		_message: InteractionMessage,
		_options: { toolCallIndex?: number } = {}
	): MessageDebugView | null {
		return null;
	}

	function llmSourceLabel(source: string): string {
		if (source === 'target') return 'Target model';
		if (source === 'auditor') return 'Tester model';
		if (source === 'tool_simulator') return 'Tool simulator';
		return source || 'LLM';
	}

	function llmApiModeLabel(apiMode: string): string {
		if (apiMode === 'chat_completion') return 'chat completion';
		if (apiMode === 'responses') return 'responses';
		return apiMode || 'unknown';
	}

	function scrollToMessageId(messageId: string | null | undefined, messages: InteractionMessage[], toolFocus?: ToolFocusState | null) {
		if (!messageId) return;
		const message = messages.find((entry) => entry.id === messageId);
		if (!message) return;
		if (message.role !== 'system' && message.judgeTurn != null) {
			scrollToTurn(message.judgeTurn, message.id, toolFocus ?? null);
			return;
		}
		const anchorId = messageAnchorId(message.id);
		if (!anchorId) return;
		const el = document.getElementById(anchorId);
		if (!el) return;
		applyMessageHighlight(null, message.id, toolFocus ?? null);
		revealMessageCard(el as HTMLElement);
		el.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}

	function findCitationByIndex(citations: AuditCitation[], index: number): AuditCitation | null {
		return citations.find((citation) => citation.index === index) ?? null;
	}

	function pickCitationPartForNavigation(citation: AuditCitation, messages: InteractionMessage[]): AuditCitationPart | null {
		const validMessageIds = new Set(messages.map((msg) => msg.id).filter((msgId): msgId is string => Boolean(msgId)));
		const parts = citation.parts ?? [];
		const resolved = parts.find((part) =>
			part.message_id &&
			validMessageIds.has(part.message_id) &&
			part.resolution?.status === 'resolved'
		);
		if (resolved) return resolved;
		const fallback = parts.find((part) => part.message_id && validMessageIds.has(part.message_id));
		return fallback ?? null;
	}

	function focusFromCitationPart(part: AuditCitationPart): ToolFocusState | null {
		if (!part.message_id) return null;
		const sourceKind = (part.source_kind ?? 'message') as AuditCitationSourceKind;
		return {
			messageId: part.message_id,
			sourceKind,
			toolArg: part.tool_arg ?? null,
			toolCallId: part.tool_call_id ?? null
		};
	}

	function scrollToCitationIndices(indices: number[], citations: AuditCitation[], messages: InteractionMessage[]) {
		for (const index of indices) {
			const citation = findCitationByIndex(citations, index);
			if (!citation) continue;
			const part = pickCitationPartForNavigation(citation, messages);
			if (!part?.message_id) continue;
			scrollToMessageId(part.message_id, messages, focusFromCitationPart(part));
			return;
		}
	}

	function resolveCitationsByIndices(indices: number[], citations: AuditCitation[]): AuditCitation[] {
		const selected: AuditCitation[] = [];
		for (const index of indices) {
			const citation = findCitationByIndex(citations, index);
			if (!citation) continue;
			if (!selected.includes(citation)) selected.push(citation);
		}
		return selected;
	}

	function showCitationTooltipForButton(button: HTMLElement, citations: AuditCitation[]) {
		const indices = parseCitationIndices(button.dataset.citationIndices);
		if (indices.length === 0) {
			hideCitationTooltip();
			return;
		}
		const selected = resolveCitationsByIndices(indices, citations);
		if (selected.length === 0) {
			hideCitationTooltip();
			return;
		}
		const rect = button.getBoundingClientRect();
		citationTooltipX = Math.max(14, Math.min(window.innerWidth - 14, rect.left + rect.width / 2));
		citationTooltipY = Math.max(12, rect.top - 10);
		citationTooltipCitations = selected;
		citationTooltipVisible = true;
	}

	function handleReferenceClick(
		e: MouseEvent,
		citations: AuditCitation[],
		messages: InteractionMessage[]
	) {
		const citationBtn = (e.target as HTMLElement).closest('.citation-ref') as HTMLElement | null;
		if (!citationBtn) return;
		const indices = parseCitationIndices(citationBtn.dataset.citationIndices);
		if (indices.length === 0) return;
		const selected = resolveCitationsByIndices(indices, citations);
		if (selected.length === 0) return;
		displayedCitations = selected;
		scrollToCitationIndices(indices, citations, messages);
	}

	function handleReferenceMouseOver(e: MouseEvent, citations: AuditCitation[]) {
		const citationBtn = (e.target as HTMLElement).closest('.citation-ref') as HTMLElement | null;
		if (!citationBtn) return;
		clearCitationTooltipHideTimer();
		showCitationTooltipForButton(citationBtn, citations);
	}

	function handleReferenceMouseOut(e: MouseEvent) {
		const citationBtn = (e.target as HTMLElement).closest('.citation-ref') as HTMLElement | null;
		if (!citationBtn) return;
		const related = e.relatedTarget as HTMLElement | null;
		if (related?.closest('.citation-ref') || related?.closest('.citation-tooltip')) return;
		scheduleCitationTooltipHide();
	}

	function handleTooltipMouseEnter() {
		clearCitationTooltipHideTimer();
	}

	function handleTooltipMouseLeave() {
		hideCitationTooltip();
	}

	function openTooltipCitation(citation: AuditCitation, citations: AuditCitation[], messages: InteractionMessage[]) {
		if (typeof citation.index !== 'number') return;
		displayedCitations = [citation];
		hideCitationTooltip();
		scrollToCitationIndices([citation.index], citations, messages);
	}

	function citationPreviewText(citation: AuditCitation): string {
		const snippets = (citation.parts ?? [])
			.map((part) => part.anchor?.exact?.trim() || part.quoted_text?.trim() || '')
			.filter((text) => text.length > 0);
		if (snippets.length === 0) return '';
		const uniqueSnippets = [...new Set(snippets)];
		const combined = uniqueSnippets.join(' ... ');
		if (combined.length <= 220) return combined;
		return combined.slice(0, 219) + '...';
	}

	function citationSourceLabel(citation: AuditCitation): string {
		const part =
			(citation.parts ?? []).find((entry) => entry.resolution?.status === 'resolved') ??
			(citation.parts ?? [])[0];
		if (!part) return 'transcript';
		if (part.source_kind === 'tool_arg') {
			return part.tool_arg ? `tool arg: ${part.tool_arg}` : 'tool arg';
		}
		if (part.source_kind === 'tool_result') return 'tool result';
		const turn = citationTurnNumber(part.message_id);
		return turn !== null ? `turn ${turn}` : 'message';
	}

	function citationTurnNumber(messageId?: string | null): number | null {
		if (!messageId) return null;
		const idx = item.messages.findIndex((m) => m.id === messageId);
		if (idx < 0) return null;
		return fallbackTurnLabel(item.messages, idx);
	}

	function citationStatusLabel(citation: AuditCitation): string | null {
		const parts = citation.parts ?? [];
		if (parts.some((part) => part.resolution?.status === 'resolved')) return null;
		if (parts.some((part) => part.resolution?.status === 'ambiguous')) return 'ambiguous';
		if (parts.some((part) => part.resolution?.status === 'unresolved')) return 'degraded';
		return null;
	}

	function conversationTurnCount(messages: InteractionMessage[]): number {
		const turns = new Set<number>();
		for (const message of messages) {
			if (typeof message.judgeTurn === 'number') turns.add(message.judgeTurn);
		}
		return turns.size;
	}

	function visibleNodeJudgments(nodeJudgments: NodeJudgment[]): NodeJudgment[] {
		const relevantNodes = nodeJudgments.filter((node) => node.relevant);
		return relevantNodes.length > 0 ? relevantNodes : nodeJudgments;
	}

	function policyNodeName(node: NodeJudgment): string | null {
		const embeddedName = typeof node.node_name === 'string' ? node.node_name.trim() : '';
		return embeddedName || null;
	}

	const hasAgenticTranscript = $derived(
		item.messages.some(
			(message: InteractionMessage) => message.role === 'tool' || (message.tool_calls?.length ?? 0) > 0
		)
	);
	const llmCallByMessageId = $derived.by(() => {
		const map = new Map<string, LlmCallTrace>();
		for (const llmCall of item.llm_calls) {
			for (const messageId of llmCall.message_ids) {
				if (!map.has(messageId)) {
					map.set(messageId, llmCall);
				}
			}
		}
		return map;
	});
	const runtimeLabel = $derived(
		runtimeModeLabel(item.target_runtime_mode, hasAgenticTranscript)
	);
	const structuredCitations = $derived(getStructuredCitations(item.verdict?.citations));
	const activeVerdict = $derived(
		(item.multi_judge?.verdicts?.[activeJudgeIndex] as Verdict | null | undefined) ??
			(item.verdict as Verdict | null | undefined)
	);
	const activeVerdictCitations = $derived(
		getActiveVerdictCitations(activeVerdict, structuredCitations)
	);
	const activeCitationWarnings = $derived(
		Array.isArray(activeVerdict?.citation_warnings)
			? activeVerdict.citation_warnings.filter((warning): warning is string => typeof warning === 'string' && warning.length > 0)
			: []
	);
	const activeJudgmentWarnings = $derived(
		Array.isArray(activeVerdict?.judgment_warnings)
			? activeVerdict.judgment_warnings.filter((warning): warning is string => typeof warning === 'string' && warning.length > 0)
			: []
	);
	const activeJudgmentWarningLabels = $derived(activeJudgmentWarnings.map(judgmentWarningLabel));
	const highlightedCitations = $derived(displayedCitations ?? []);
	const hasPerJudgeInspection = $derived((item.multi_judge?.verdicts?.length ?? 0) > 1);
	const citedMessageIds = $derived(
		new Set(
			activeVerdictCitations
				.flatMap((citation) =>
					(citation.parts ?? [])
						.filter((part) => part.resolution?.status === 'resolved')
						.map((part) => part.message_id)
				)
				.filter(Boolean)
		)
	);
	const nodeJudgments = $derived(
		Array.isArray(activeVerdict?.node_judgments)
			? visibleNodeJudgments(activeVerdict.node_judgments as NodeJudgment[])
			: []
	);
	const firstMessageIdByTurn = $derived.by(() => {
		const map = new Map<number, string>();
		for (const message of item.messages) {
			if (typeof message.judgeTurn !== 'number' || !message.id) continue;
			if (!map.has(message.judgeTurn)) map.set(message.judgeTurn, message.id);
		}
		return map;
	});
	const agentTimeline = $derived.by(() => {
		const segments: { agent: string; messageId: string }[] = [];
		let prevAgent: string | null = null;
		for (const message of item.messages) {
			if (message.role !== 'assistant') continue;
			const agent = typeof message.agent === 'string' ? message.agent.trim() : '';
			if (!agent || agent === prevAgent) continue;
			if (message.id) segments.push({ agent, messageId: message.id });
			prevAgent = agent;
		}
		return segments;
	});
	const agentByMessageId = $derived.by(() => {
		const map = new Map<string, string>();
		let prevAgent: string | null = null;
		for (const message of item.messages) {
			if (message.role !== 'assistant') continue;
			const agent = typeof message.agent === 'string' ? message.agent.trim() : '';
			if (!agent) {
				prevAgent = null;
				continue;
			}
			if (agent !== prevAgent && message.id) map.set(message.id, agent);
			prevAgent = agent;
		}
		return map;
	});

	function turnAnchorId(turnLabel: number | null, messageId: string | null | undefined): string | undefined {
		if (turnLabel == null || !messageId) return undefined;
		const firstId = firstMessageIdByTurn.get(turnLabel);
		return firstId === messageId ? `drawer-turn-${turnLabel}` : undefined;
	}

	function scrollToMessageAnchor(messageId: string) {
		const anchorId = messageAnchorId(messageId);
		const el = anchorId ? document.getElementById(anchorId) : null;
		if (!el) return;
		revealMessageCard(el);
		applyMessageHighlight(null, messageId, null);
		el.scrollIntoView({ behavior: 'smooth', block: 'center' });
	}
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm transition-opacity" onclick={onClose}></div>

<div class="fixed inset-4 z-50 mx-auto flex max-w-7xl flex-col overflow-hidden rounded-xl border border-border bg-bg shadow-2xl">
	<div class="flex items-center gap-3 border-b border-border px-6 py-3 flex-shrink-0">
		<button
			aria-label="Close details"
			class="flex h-8 w-8 items-center justify-center rounded-lg text-text-muted transition-colors hover:bg-surface hover:text-text"
			onclick={onClose}
		>
			<svg class="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M6 18L18 6M6 6l12 12"/></svg>
		</button>
		<div class="flex items-center gap-0.5">
			<button class="flex h-7 w-7 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-surface hover:text-text disabled:opacity-25 disabled:pointer-events-none" disabled={navIdx <= 0} onclick={onPrev} title="Previous (←)">
				<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M15 19l-7-7 7-7"/></svg>
			</button>
			<span class="min-w-[3rem] text-center text-[10px] tabular-nums text-text-muted">{navIdx + 1} / {navTotal}</span>
			<button class="flex h-7 w-7 items-center justify-center rounded-md text-text-muted transition-colors hover:bg-surface hover:text-text disabled:opacity-25 disabled:pointer-events-none" disabled={navIdx >= navTotal - 1} onclick={onNext} title="Next (→)">
				<svg class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
			</button>
		</div>
		<div class="flex-1 min-w-0">
			<h2 class="truncate text-sm font-medium text-text" title={item.header_title}>{item.header_title}</h2>
			<div class="mt-0.5 flex flex-wrap items-center gap-2">
				{#if item.kind === 'scenario'}
					<span class="text-xs text-text-muted">{conversationTurnCount(item.messages)} turns</span>
					<span class="text-xs text-text-muted">·</span>
				{/if}
				{#if item.kind === 'scenario' && item.context.stop_reason}
					<span class={stopReasonChipClass(item.context.stop_reason_display)} title={stopReasonTitle(item.context.stop_reason, item.context.stop_reason_display)}>
						{stopReasonLabel(item.context.stop_reason, item.context.stop_reason_display)}
					</span>
					<span class="text-xs text-text-muted">·</span>
				{/if}

				{#if judgeStatus(item) === 'judge_failed'}
					<span class="rounded px-1.5 py-0.5 text-[10px] font-medium bg-amber-500/10 text-amber-400">judge failed</span>
				{:else if judgeStatus(item) === 'unjudged'}
					<span class="rounded px-1.5 py-0.5 text-[10px] font-medium bg-surface-2 text-text-muted">unjudged</span>
				{/if}
				{#if item.dimensions}
					<span class="flex flex-wrap items-center gap-1.5">
						{#each Object.entries(item.dimensions) as [name, value]}
							<span class="inline-flex items-center rounded-full bg-zinc-700 px-2 py-0.5 text-[10px] font-medium text-zinc-200">
								{formatFactorLabel(name)}: {value}
							</span>
						{/each}
					</span>
				{/if}
			</div>
		</div>
		<div class="flex items-center gap-1.5">
			{#each metricNames as m}
				{@const v = getRecordFlag(item, m)}
				{#if v !== null}
					<span class="inline-flex items-center gap-1 rounded bg-surface-2 px-2 py-1 text-xs">
						<span class="text-text-muted">{metricLabel(m)}</span>
						<span class="font-semibold tabular-nums {metricOutcomeClass(v)}">{metricOutcomeText(v)}</span>
					</span>
				{/if}
			{/each}
			{#if item.multi_judge}
				<div class="flex items-center gap-0.5 ml-1">
					{#each item.multi_judge.votes?.[primaryMetric] ?? [] as vote}
						{@const agreed = vote === getRecordFlag(item, primaryMetric)}
						<span class="inline-block size-[7px] rounded-full" style={agreed ? `background: ${metricDotColor(vote)}` : `background: transparent; box-shadow: inset 0 0 0 1.5px ${metricDotColor(vote)}`}></span>
					{/each}
				</div>
			{/if}
		</div>
	</div>

	<div class="flex flex-1 min-h-0">
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<!-- svelte-ignore a11y_mouse_events_have_key_events -->
		<div
			class="w-2/5 border-r border-border overflow-y-auto p-5"
			onclick={(e) => handleReferenceClick(e, activeVerdictCitations, item.messages)}
			onmouseover={(e) => handleReferenceMouseOver(e, activeVerdictCitations)}
			onmouseout={handleReferenceMouseOut}
		>
			{#if item.kind === 'scenario' && item.context.description}
				<h3 class="mb-2 text-[24px] font-semibold text-text">Scenario</h3>
				<div class="mb-4 rounded-lg border border-border/50 bg-surface/50">
					<button class="flex w-full items-center gap-2 px-4 py-3 text-left" onclick={() => contextExpanded = !contextExpanded}>
						<span class="flex-1 truncate text-sm font-medium text-text">{item.header_title}</span>
						<svg class="h-3.5 w-3.5 flex-shrink-0 text-text-muted transition-transform {contextExpanded ? 'rotate-180' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2"><path d="M19 9l-7 7-7-7"/></svg>
					</button>
					{#if contextExpanded}
						<div class="border-t border-border/30 px-4 pb-4 pt-3">
							<p class="text-sm text-text-secondary leading-relaxed">{item.context.description}</p>
							{#if item.context.tools?.length}
								<div class="mt-3 border-t border-border/30 pt-3">
									<button class="flex items-center gap-1.5" onclick={(e) => { const el = (e.currentTarget as HTMLElement).nextElementSibling; if (el) el.classList.toggle('hidden'); const chevron = (e.currentTarget as HTMLElement).querySelector('.chevron'); if (chevron) chevron.classList.toggle('rotate-90'); }}>
										<svg class="chevron h-3 w-3 text-text-muted/60 transition-transform duration-150" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
										<h4 class="text-xs font-semibold uppercase tracking-widest text-text-muted">Tools</h4>
										<span class="text-[10px] text-text-muted">{item.context.tools.map((tool: SeedTool) => tool.name).join(', ')}</span>
									</button>
									<div class="hidden mt-2 space-y-2">
										{#each item.context.tools as tool}
											<div class="rounded border border-border/30 bg-bg/50 px-3 py-2">
												<span class="font-mono text-xs font-semibold text-text">{tool.name}</span>
												{#if tool.description}
													<p class="mt-0.5 text-xs text-text-secondary">{tool.description}</p>
												{/if}
												{#if tool.parameters?.length}
													<div class="mt-1.5 flex flex-wrap gap-1">
														{#each tool.parameters as parameter}
															<span class="inline-flex items-center rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] text-text-muted border border-border/20">
																{parameter.name}<span class="text-text-muted/50 ml-0.5">:{parameter.type || 'string'}</span>
															</span>
														{/each}
													</div>
												{/if}
											</div>
										{/each}
									</div>
								</div>
							{/if}
						</div>
					{/if}
				</div>
			{/if}
			<h3 class="mb-3 text-[24px] font-semibold text-text">Judgment</h3>
			<div class="space-y-3">
				{#if judgeStatus(item) === 'unjudged'}
					<div class="rounded-lg border border-border bg-surface p-4">
						<div class="text-xs font-semibold uppercase tracking-wider text-text-muted">Unjudged preview</div>
						<p class="mt-2 text-sm text-text-secondary leading-relaxed">
							This conversation finished rollout and is available for inspection, but the judge has not scored it yet.
						</p>
					</div>
				{/if}
				{#if judgeStatus(item) === 'judge_failed'}
					<div class="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
						<div class="text-xs font-semibold uppercase tracking-wider text-amber-400">Judge failed</div>
						<p class="mt-2 text-sm text-text-secondary leading-relaxed">
							{getJudgeError(item) ?? 'The judge did not produce a valid scored verdict for this result.'}
						</p>
					</div>
				{/if}
				{#if activeJudgmentWarnings.length > 0}
					<div class="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
						<div class="text-xs font-semibold uppercase tracking-wider text-amber-400">Judgment inconsistent</div>
						<p class="mt-2 text-sm text-text-secondary leading-relaxed">
							The judgment was kept, but parts of it disagree internally. Node-level policy findings and the top-level policy decision do not fully match for: {activeJudgmentWarningLabels.join('; ')}.
						</p>
					</div>
				{/if}
				{#if hasPerJudgeInspection}
					<div class="rounded-lg border border-zinc-800 bg-zinc-900/50">
						<div class="flex flex-wrap gap-1 border-b border-zinc-800 p-1.5">
							{#each item.multi_judge?.verdicts ?? [] as judgeVerdict, i}
								{@const vote = getVerdictFlag(judgeVerdict, primaryMetric)}
								<button
									class="flex items-center gap-1.5 rounded px-2.5 py-1 text-[10px] font-medium transition-colors {activeJudgeIndex === i ? 'bg-zinc-800 text-zinc-100' : 'text-zinc-500 hover:text-zinc-300'}"
									onclick={() => {
										activeJudgeIndex = i;
										displayedCitations = null;
										highlightedTurn = null;
									}}
								>
									<span
										class="inline-block size-[6px] rounded-full shrink-0"
										style={vote === null
											? 'background: transparent; box-shadow: inset 0 0 0 1.5px var(--theme-text-muted);'
											: `background: ${metricDotColor(vote)}`}
									></span>
									Judge {i + 1}
									<span class="opacity-50">· {metricOutcomeText(vote)}</span>
								</button>
							{/each}
						</div>
						<p class="px-3 py-2 text-xs leading-relaxed text-zinc-400">
							Inspecting Judge {activeJudgeIndex + 1}. Dimension explanations, policy reasoning, and transcript highlights follow this verdict.
						</p>
					</div>
				{/if}
				{#if activeVerdict?.narrative}
					<div class="rounded-lg border border-border bg-surface p-4">
						<div class="mb-2 text-xs font-semibold text-text-muted">Conversation summary</div>
						<p class="text-sm text-text-secondary leading-relaxed">{activeVerdict.narrative}</p>
					</div>
				{/if}
				{#each metricNames as m}
					{@const v = getVerdictFlag(activeVerdict, m)}
					{@const dimensionJustification = getDimensionJustification(activeVerdict, m)}
					{#if v !== null}
						<div class="rounded-lg border border-border bg-surface p-4">
							<div class="mb-2 flex items-center justify-between">
								<span class="text-[16px] font-semibold text-text">{metricLabel(m)}</span>
								<div class="flex items-center gap-2">
									{#if item.multi_judge && item.multi_judge.votes?.[m]}
										<div class="flex items-center gap-0.5">
											{#each item.multi_judge.votes[m] as vote}
												{@const agreed = vote === v}
												<span class="inline-block size-[7px] rounded-full" style={agreed ? `background: ${metricDotColor(vote)}` : `background: transparent; box-shadow: inset 0 0 0 1.5px ${metricDotColor(vote)}`}></span>
											{/each}
										</div>
										<span class="text-[10px] text-text-muted tabular-nums">Flagged {metricRateText(item.multi_judge.means?.[m] ?? 0)}</span>
									{/if}
									<span class="text-[16px] font-bold tabular-nums {metricOutcomeClass(v)}">{metricOutcomeText(v)}</span>
								</div>
							</div>
							{#if dimensionJustification}
								<div class="text-sm text-text-secondary leading-relaxed prose max-w-none citation-prose">{@html renderTextWithCitationButtons(dimensionJustification)}</div>
							{:else if m === primaryMetric && activeVerdict?.justification}
								<div class="text-sm text-text-secondary leading-relaxed prose max-w-none citation-prose">{@html renderTextWithCitationButtons(activeVerdict.justification as string)}</div>
							{/if}
								{#if m === 'policy_violation' && nodeJudgments.length > 0}
									<div class="mt-3 space-y-1.5 border-t border-border/50 pt-3">
										{#each nodeJudgments as node}
											{@const violated = node.violated}
											{@const nodeName = policyNodeName(node)}
											<div class="rounded-md px-3 py-2 {violated ? 'bg-score-fail/5' : violated === null ? 'bg-surface-2/50' : 'bg-score-pass/5'}">
												<div class="flex items-start justify-between gap-2">
													<div class="min-w-0 flex-1">
														<div
															class="truncate text-xs font-semibold text-text-muted"
															title={nodeName ?? 'Unnamed policy node'}
														>
															{nodeName ?? 'Unnamed policy node'}
														</div>
													</div>
													<div class="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
														<span class="inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium {violated ? 'bg-score-fail/10 text-score-fail' : violated === null ? 'bg-surface-2 text-text-muted' : 'bg-score-pass/10 text-score-pass'}">
															{violated === null ? 'Not assessed' : violated ? 'Flagged' : 'Pass'}
														</span>
														{#if node.confidence}
															{@const conf = node.confidence}
															{@const confColor = conf === 'high' ? 'var(--color-score-pass, #1a7f37)' : conf === 'medium' ? '#d29922' : 'var(--color-score-fail, #cf222e)'}
															<span class="inline-flex items-center gap-1 text-[10px] text-text-muted" title="{conf} confidence">
																{#if conf === 'high'}
																	<!-- Outline circle with check -->
																	<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke={confColor} stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
																		<circle cx="8" cy="8" r="6.5"/>
																		<path d="M5 8.25 7.25 10.5l4-4.5"/>
																	</svg>
																{:else}
																	<!-- Outline triangle with exclamation -->
																	<svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke={confColor} stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
																		<path d="M8 2.25 14.25 13H1.75L8 2.25Z"/>
																		<path d="M8 6.5v3"/>
																		<circle cx="8" cy="11.4" r="0.55" fill={confColor} stroke="none"/>
																	</svg>
																{/if}
																<span>{conf} confidence</span>
															</span>
														{/if}
													</div>
												</div>
												{#if node.reasoning}
													<div class="mt-1.5 text-sm text-text-secondary leading-relaxed prose max-w-none citation-prose">{@html renderTextWithCitationButtons(node.reasoning)}</div>
												{/if}
											</div>
										{/each}
									</div>
								{/if}
							</div>
						{/if}
					{/each}
				</div>
			</div>

		{#if citationTooltipVisible && citationTooltipCitations.length > 0}
			<div
				class="citation-tooltip"
				style={`left: ${citationTooltipX}px; top: ${citationTooltipY}px;`}
				role="tooltip"
				onmouseenter={handleTooltipMouseEnter}
				onmouseleave={handleTooltipMouseLeave}
			>
				<div class="citation-tooltip-list">
					{#each citationTooltipCitations as citation}
						{@const preview = citationPreviewText(citation)}
						{@const status = citationStatusLabel(citation)}
						<button
							type="button"
							class="citation-tooltip-item"
							onclick={() => openTooltipCitation(citation, activeVerdictCitations, item.messages)}
						>
							<div class="citation-tooltip-head">
								<span class="citation-tooltip-index">[{citation.index}]</span>
								<span class="citation-tooltip-source">{citationSourceLabel(citation)}</span>
								{#if status}
									<span class="citation-tooltip-status">{status}</span>
								{/if}
							</div>
							{#if preview}
								<div class="citation-tooltip-preview">"{preview}"</div>
							{/if}
							{#if citation.description}
								<div class="citation-tooltip-description">{citation.description}</div>
							{/if}
						</button>
					{/each}
				</div>
			</div>
		{/if}

		<div class="w-3/5 overflow-y-auto p-5">
			<h3 class="mb-4 text-[24px] font-semibold text-text">
				Conversation · {conversationTurnCount(item.messages)} turns
			</h3>
			{#if agentTimeline.length >= 2}
				<div class="mb-4 flex flex-wrap items-center gap-1 rounded-md border border-border/60 bg-surface-2/40 px-3 py-2 text-[11px] text-text-muted">
					<span class="font-semibold uppercase tracking-wide text-text-muted/80">Agents</span>
					{#each agentTimeline as segment, segmentIndex}
						{#if segmentIndex > 0}
							<span aria-hidden="true" class="text-text-muted/50">→</span>
						{/if}
						<button
							type="button"
							class="rounded bg-surface px-1.5 py-0.5 font-mono text-[11px] text-text hover:bg-surface-2 transition-colors"
							title="Jump to first message from {segment.agent}"
							onclick={() => scrollToMessageAnchor(segment.messageId)}
						>
							{segment.agent}
						</button>
					{/each}
				</div>
			{/if}
			{#if item.kind === 'scenario' && item.context.stop_reason_display}
				<div class="mb-4 rounded-lg border border-amber-500/20 bg-amber-500/5 px-4 py-3">
					<div class="text-xs font-semibold uppercase tracking-wide text-amber-400">
						{item.context.stop_reason_display.label}
					</div>
					<p class="mt-1 text-sm text-text-secondary">{item.context.stop_reason_display.description}</p>
					{#if item.context.stop_reason}
						<p class="mt-2 text-[11px] text-text-muted">
							Stop reason: <code class="rounded bg-black/20 px-1 py-0.5">{item.context.stop_reason}</code>
						</p>
					{/if}
				</div>
			{/if}
			<div class="space-y-3">
				{#each item.messages as message, messageIndex}
					{@const turnLabel = message.role === 'system' ? null : (message.judgeTurn ?? fallbackTurnLabel(item.messages, messageIndex))}
					{@const messageCitations = citationPartsForMessage(highlightedCitations, message.id)}
					{@const isCited = Boolean(message.id && citedMessageIds.has(message.id))}
					{@const isHighlighted = (turnLabel != null && highlightedTurn === turnLabel) || (message.id != null && highlightedMessageId === message.id)}
					{@const isToolCall = message.type === 'tool_call' || message.role === 'tool'}
					{@const isSystem = message.role === 'system'}
					{@const isUser = message.role === 'user'}
					{@const isSystemPromptSet = message.type === 'set_system_message'}
					{@const messageLlmCall = getMessageLlmCall(message)}
					{@const messageDebug = getMessageDebugView(message)}
					{@const bodyKey = messagePanelKey(message, messageIndex, 'body')}
					{@const rawPanelKey = messagePanelKey(message, messageIndex, 'raw')}
					{#if isSystem}
						<div
							class="flex gap-3 flex-row-reverse"
							id={messageAnchorId(message.id)}
							data-message-body-key={bodyKey}
						>
							<div class="flex-shrink-0 mt-1">
								<div class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold bg-zinc-500/15 text-zinc-400">S</div>
							</div>
							<div class="w-[85%] rounded-lg bg-zinc-500/8 border border-zinc-500/25 {isHighlighted ? 'ring-2 ring-interactive' : ''} overflow-hidden">
								<button
									class="w-full flex items-center gap-2 px-4 py-2.5 text-left hover:bg-zinc-500/10 transition-colors"
									onclick={() => toggleMessageBody(bodyKey)}
								>
									<svg class="chevron h-3 w-3 text-zinc-400/70 transition-transform duration-150 {hasExpandedBody(bodyKey) ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
									<span class="text-xs font-semibold text-text-muted">{isSystemPromptSet ? 'System prompt set' : 'System prompt'}</span>
									{#if isCited}
										<span class="inline-flex items-center rounded-full bg-score-border/15 px-2 py-0.5 text-[10px] font-semibold text-score-border">cited</span>
									{/if}
									{#if messageLlmCall}
										<span class="ml-auto text-[10px] font-mono font-bold uppercase tracking-wide text-zinc-400/70">llm</span>
									{:else if messageDebug}
										<span class="ml-auto text-[10px] font-medium text-zinc-400/70">{messageDebug.label}</span>
									{/if}
								</button>
								{#if hasExpandedBody(bodyKey)}
									<div data-message-collapse-body class="px-4 pb-3 border-t border-zinc-500/15">
										<div class="text-sm text-text-secondary leading-relaxed prose max-w-none pt-2.5">{@html renderMessageHtml(message, messageCitations)}</div>
										{#if messageLlmCall}
											<div class="mt-3 rounded border border-zinc-500/20 bg-black/20 p-3">
												<div class="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-zinc-400/80">
													<span>LLM Call</span>
													<span class="text-text-muted">{llmSourceLabel(messageLlmCall.source)} · {llmApiModeLabel(messageLlmCall.api_mode)}</span>
												</div>
												<div class="mt-3">
													<div class="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">Request</div>
													<pre class="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-all text-[11px] font-mono text-text-muted">{JSON.stringify(messageLlmCall.request, null, 2)}</pre>
												</div>
												<div class="mt-3 border-t border-zinc-500/15 pt-3">
													<div class="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">Response</div>
													<pre class="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-all text-[11px] font-mono text-text-muted">{JSON.stringify(messageLlmCall.response, null, 2)}</pre>
												</div>
											</div>
										{:else if messageDebug}
											<div class="mt-3 rounded border border-zinc-500/20 bg-black/20 p-3">
												<div class="text-[11px] font-semibold uppercase tracking-wide text-zinc-400/80">{messageDebug.panelTitle}</div>
												<p class="mt-1 text-[11px] leading-relaxed text-text-muted">{messageDebug.description}</p>
												<pre class="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap break-all text-[11px] font-mono text-text-muted">{JSON.stringify(messageDebug.payload, null, 2)}</pre>
											</div>
										{/if}
									</div>
								{/if}
							</div>
						</div>
					{:else if message.role === 'assistant' && message.tool_calls && message.tool_calls.length > 0}
						{@const assistantAgentBadge = message.id ? agentByMessageId.get(message.id) : undefined}
						{#each message.tool_calls as toolCall, toolCallIndex}
							{@const toolCallLlmCall = getMessageLlmCall(message, { toolCallIndex })}
							{@const toolCallDebug = getMessageDebugView(message, { toolCallIndex })}
							{@const toolCallBodyKey = messagePanelKey(message, messageIndex, `tool-call-${toolCallIndex}`)}
							<div
								class="flex gap-3 flex-row-reverse"
								id={toolCallIndex === 0
									? (turnAnchorId(turnLabel, message.id) ?? messageAnchorId(message.id))
									: undefined}
								data-message-id={toolCallIndex === 0 ? message.id : undefined}
								data-tool-call-anchor={toolCallAnchorId(message.id, toolCall.id)}
								data-message-body-key={toolCallBodyKey}
							>
								<div class="flex-shrink-0 mt-1">
									<div class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold bg-text-muted/20 text-text">⚡</div>
								</div>
								<div class="w-[85%] rounded-lg bg-text-muted/12 border border-border/60 overflow-hidden">
									<button
										class="w-full flex items-center gap-2 px-4 py-2.5 text-left hover:bg-text-muted/15 transition-colors"
										onclick={() => toggleMessageBody(toolCallBodyKey)}
									>
										<svg class="chevron h-3 w-3 text-text-muted transition-transform duration-150 {hasExpandedBody(toolCallBodyKey) ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
										<span class="text-xs font-semibold text-text-muted">Tool call</span>
										<span class="text-xs font-mono text-text-muted">{toolCall.function}</span>
										{#if toolCallIndex === 0 && assistantAgentBadge}
											<span
												class="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] text-text-muted"
												title="Sub-agent that produced this assistant turn"
											>{assistantAgentBadge}</span>
										{/if}
										{#if toolCallLlmCall}
											<span class="ml-auto text-[10px] font-mono font-bold uppercase tracking-wide text-text-muted">llm</span>
										{:else if toolCallDebug}
											<span class="ml-auto text-[10px] font-mono font-bold uppercase tracking-wide text-text-muted">{toolCallDebug.label}</span>
										{/if}
									</button>
									{#if hasExpandedBody(toolCallBodyKey)}
										<div data-message-collapse-body class="px-4 pb-3 border-t border-border/50">
											<pre class="pt-2.5 text-xs text-text-secondary bg-bg/50 rounded p-2 overflow-x-auto {highlightedToolFocus?.messageId === message.id && highlightedToolFocus?.sourceKind === 'tool_arg' && (!highlightedToolFocus.toolCallId || highlightedToolFocus.toolCallId === toolCall.id) ? 'citation-tool-arg-focus citation-focused-block' : ''}">{JSON.stringify(toolCall.arguments, null, 2)}</pre>
											{#if toolCallLlmCall}
												<div class="mt-3 rounded border border-border/50 bg-bg/50 p-3">
													<div class="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
														<span>LLM Call</span>
														<span class="text-text-muted">{llmSourceLabel(toolCallLlmCall.source)} · {llmApiModeLabel(toolCallLlmCall.api_mode)}</span>
													</div>
													<div class="mt-3">
														<div class="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">Request</div>
														<pre class="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-all text-[11px] font-mono text-text-muted">{JSON.stringify(toolCallLlmCall.request, null, 2)}</pre>
													</div>
													<div class="mt-3 border-t border-border/50 pt-3">
														<div class="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">Response</div>
														<pre class="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-all text-[11px] font-mono text-text-muted">{JSON.stringify(toolCallLlmCall.response, null, 2)}</pre>
													</div>
												</div>
											{:else if toolCallDebug}
												<div class="mt-3 rounded border border-border/50 bg-bg/50 p-3">
													<div class="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{toolCallDebug.panelTitle}</div>
													<p class="mt-1 text-[11px] leading-relaxed text-text-muted">{toolCallDebug.description}</p>
													<pre class="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap break-all text-[11px] font-mono text-text-muted">{JSON.stringify(toolCallDebug.payload, null, 2)}</pre>
												</div>
											{/if}
										</div>
									{/if}
								</div>
							</div>
						{/each}
					{:else if isToolCall}
						<div
							class="flex gap-3 flex-row-reverse"
							id={turnAnchorId(turnLabel, message.id) ?? messageAnchorId(message.id)}
							data-message-id={message.id}
							data-message-body-key={bodyKey}
						>
							<div class="flex-shrink-0 mt-1">
								<div class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold bg-text-muted/20 text-text">⚙</div>
							</div>
							<div class="w-[85%] rounded-lg bg-text-muted/12 border border-border/60 {isHighlighted ? 'ring-2 ring-interactive' : ''} {highlightedToolFocus?.messageId === message.id ? (highlightedToolFocus?.sourceKind === 'tool_arg' ? 'citation-tool-arg-focus' : highlightedToolFocus?.sourceKind === 'tool_result' ? 'citation-tool-result-focus' : 'citation-tool-message-focus') : ''} overflow-hidden">
								<button
									class="w-full flex items-center gap-2 px-4 py-2.5 text-left hover:bg-text-muted/15 transition-colors"
									onclick={() => toggleMessageBody(bodyKey)}
								>
									<svg class="chevron h-3 w-3 text-text-muted transition-transform duration-150 {hasExpandedBody(bodyKey) ? 'rotate-90' : ''}" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="3"><path d="M9 5l7 7-7 7"/></svg>
									<span class="text-xs font-semibold text-text-muted">Tool result{turnLabel != null ? ` · Turn ${turnLabel}` : ''}</span>
									{#if isCited}
										<span class="inline-flex items-center rounded-full bg-score-border/15 px-2 py-0.5 text-[10px] font-semibold text-score-border">cited</span>
									{/if}
									{#if message.function}
										<span class="text-xs font-mono text-text-muted">{message.function}</span>
									{/if}
									{#if messageLlmCall}
										<span class="ml-auto text-[10px] font-mono font-bold uppercase tracking-wide text-text-muted">llm</span>
									{:else if messageDebug}
										<span class="ml-auto text-[10px] font-medium text-text-muted">{messageDebug.label}</span>
									{/if}
								</button>
								{#if hasExpandedBody(bodyKey)}
									<div data-message-collapse-body class="px-4 pb-3 border-t border-border/50">
										<div class="text-sm text-text leading-relaxed prose max-w-none pt-2.5 {highlightedToolFocus?.messageId === message.id ? 'citation-focused-block' : ''}">{@html renderMessageHtml(message, messageCitations)}</div>
										{#if messageLlmCall}
											<div class="mt-3 rounded border border-border/50 bg-bg/50 p-3">
												<div class="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
													<span>LLM Call</span>
													<span class="text-text-muted">{llmSourceLabel(messageLlmCall.source)} · {llmApiModeLabel(messageLlmCall.api_mode)}</span>
												</div>
												<div class="mt-3">
													<div class="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">Request</div>
													<pre class="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-all text-[11px] font-mono text-text-muted">{JSON.stringify(messageLlmCall.request, null, 2)}</pre>
												</div>
												<div class="mt-3 border-t border-border/50 pt-3">
													<div class="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">Response</div>
													<pre class="mt-2 max-h-80 overflow-y-auto whitespace-pre-wrap break-all text-[11px] font-mono text-text-muted">{JSON.stringify(messageLlmCall.response, null, 2)}</pre>
												</div>
											</div>
										{:else if messageDebug}
											<div class="mt-3 rounded border border-border/50 bg-bg/50 p-3">
												<div class="text-[11px] font-semibold uppercase tracking-wide text-text-muted">{messageDebug.panelTitle}</div>
												<p class="mt-1 text-[11px] leading-relaxed text-text-muted">{messageDebug.description}</p>
												<pre class="mt-3 max-h-80 overflow-y-auto whitespace-pre-wrap break-all text-[11px] font-mono text-text-muted">{JSON.stringify(messageDebug.payload, null, 2)}</pre>
											</div>
										{/if}
									</div>
								{/if}
							</div>
						</div>
					{:else}
						{@const regularAgentBadge = !isUser && message.id ? agentByMessageId.get(message.id) : undefined}
						<div
							id={turnAnchorId(turnLabel, message.id) ?? messageAnchorId(message.id)}
							data-message-id={message.id}
							class="flex gap-3 {isUser ? '' : 'flex-row-reverse'} transition-all duration-300"
						>
							<div class="flex-shrink-0 mt-1">
								<div class="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold {isUser ? 'bg-interactive/15 text-interactive' : 'bg-surface-2 text-text-muted'}">
									{isUser ? (item.kind === 'prompt' ? 'U' : 'A') : 'T'}
								</div>
							</div>
							<div class="{isUser ? 'max-w-[85%]' : 'w-[85%]'} rounded-lg {isUser ? 'bg-interactive/8' : 'bg-surface-2'} {isHighlighted ? 'ring-2 ring-interactive bg-interactive/12' : ''} overflow-hidden">
								<div class="flex items-center gap-2 px-4 pt-3 pb-1.5">
									<span class="text-xs font-semibold text-text-muted">{isUser ? (item.kind === 'prompt' ? 'User' : 'Tester') : 'Target'}{turnLabel != null ? ` · Turn ${turnLabel}` : ''}</span>
									{#if regularAgentBadge}
										<span
											class="rounded bg-surface px-1.5 py-0.5 font-mono text-[10px] text-text-muted"
											title="Sub-agent that produced this assistant turn"
										>{regularAgentBadge}</span>
									{/if}
									{#if isCited}
										<span class="inline-flex items-center rounded-full bg-score-border/15 px-2 py-0.5 text-[10px] font-semibold text-score-border">cited</span>
									{/if}
									{#if messageLlmCall}
										<button
											class="ml-auto rounded px-1.5 py-0.5 text-[10px] font-mono font-bold uppercase tracking-wide transition-colors {isRawPanelOpen(rawPanelKey) ? 'bg-interactive/15 text-interactive ring-1 ring-interactive/30' : 'text-text-muted/70 hover:text-text-muted hover:bg-surface'}"
											title="View owned LLM request and response"
											onclick={() =>
												toggleRawPanel(rawPanelKey, {
													request: messageLlmCall.request,
													response: messageLlmCall.response
												})}
										>
											llm
										</button>
									{:else if messageDebug}
										<button
											class="ml-auto rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors {isRawPanelOpen(rawPanelKey) ? 'bg-interactive/15 text-interactive ring-1 ring-interactive/30' : 'text-text-muted/70 hover:text-text-muted hover:bg-surface'}"
											title={messageDebug.buttonTitle}
											onclick={() =>
												toggleRawPanel(rawPanelKey, {
													payload: messageDebug.payload
												})}
										>
											{messageDebug.label}
										</button>
									{/if}
								</div>
								<div class="px-4 pb-3">
									<div class="text-sm text-text leading-relaxed prose max-w-none">{@html renderMessageHtml(message, messageCitations)}</div>
								</div>
								{#if messageLlmCall && isRawPanelOpen(rawPanelKey)}
									{@const rawPanel = rawPanels[rawPanelKey]}
									<div class="border-t border-border px-4 py-3 bg-black/20">
										<div class="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-text-secondary">
											<span>LLM Call</span>
											<span class="text-text-muted">{llmSourceLabel(messageLlmCall.source)} · {llmApiModeLabel(messageLlmCall.api_mode)}</span>
										</div>
										<div class="mt-3">
											<div class="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">Request</div>
											<pre class="mt-2 text-[11px] text-text-muted font-mono whitespace-pre-wrap break-all max-h-80 overflow-y-auto">{rawPanel?.request}</pre>
										</div>
										<div class="mt-3 border-t border-border pt-3">
											<div class="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">Response</div>
											<pre class="mt-2 text-[11px] text-text-muted font-mono whitespace-pre-wrap break-all max-h-80 overflow-y-auto">{rawPanel?.response}</pre>
										</div>
									</div>
								{:else if messageDebug && isRawPanelOpen(rawPanelKey)}
									{@const rawPanel = rawPanels[rawPanelKey]}
									<div class="border-t border-border px-4 py-3 bg-black/20">
										<div class="text-[11px] font-semibold uppercase tracking-wide text-text-secondary">{messageDebug.panelTitle}</div>
										<p class="mt-1 text-[11px] leading-relaxed text-text-muted">{messageDebug.description}</p>
										<pre class="mt-3 text-[11px] text-text-muted font-mono whitespace-pre-wrap break-all max-h-80 overflow-y-auto">{rawPanel?.payload}</pre>
									</div>
								{/if}
							</div>
						</div>
					{/if}
				{/each}
				{#if item.messages.length === 0}
					{#if !(item.kind === 'scenario' && item.context.stop_reason_display)}
						<p class="text-sm text-text-muted italic">No transcript available for this result.</p>
					{/if}
				{/if}
			</div>
		</div>
	</div>
</div>
