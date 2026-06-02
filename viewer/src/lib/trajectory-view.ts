import type { TrajectoryRow, TrajectoryStep } from '$lib/types.js';

export interface TrajectoryTraceItem {
	kind: 'message' | 'evidence';
	step: TrajectoryStep;
}

export interface TrajectoryTraceGroup {
	id: string;
	label: string;
	messages: TrajectoryStep[];
	evidence: TrajectoryStep[];
	items: TrajectoryTraceItem[];
}

function isTranscriptMessageStep(step: TrajectoryStep): boolean {
	return (step.type === 'message' || step.type === 'system') && Boolean(step.message_id || step.event_id);
}

function trajectoryActorKey(step: TrajectoryStep): string {
	return String(step.actor || step.role || step.type).toLowerCase();
}

export function trajectoryMessageLabel(step: TrajectoryStep): string {
	const actor = trajectoryActorKey(step);
	if (step.type === 'system' || actor === 'system') return 'System message';
	if (actor === 'auditor' || actor === 'tester') return 'Tester message';
	if (step.role === 'user') return 'User message';
	if (actor === 'target') return 'Target response';
	if (step.role === 'assistant') return 'Assistant response';
	return `${actor || 'agent'} message`;
}

export function trajectoryGroupSummary(group: TrajectoryTraceGroup): string {
	const parts: string[] = [];
	if (group.messages.length) {
		parts.push(`${group.messages.length} message${group.messages.length === 1 ? '' : 's'}`);
	}
	if (group.evidence.length) {
		parts.push(`${group.evidence.length} evidence step${group.evidence.length === 1 ? '' : 's'}`);
	}
	return parts.join(' · ');
}

export function trajectoryStartsCollapsed(trajectory: Pick<TrajectoryRow, 'interaction' | 'target_runtime_mode'>): boolean {
	void trajectory;
	return true;
}

export function buildTrajectoryGroups(steps: TrajectoryStep[]): TrajectoryTraceGroup[] {
	const groups: TrajectoryTraceGroup[] = [];
	const messageGroups = new Map<string, TrajectoryTraceGroup>();
	let currentExchange: TrajectoryTraceGroup | null = null;
	let exchangeIndex = 0;

	function registerMessage(group: TrajectoryTraceGroup, step: TrajectoryStep): void {
		const messageKey = step.message_id || step.event_id;
		if (messageKey) messageGroups.set(messageKey, group);
	}

	for (const step of steps) {
		if (!isTranscriptMessageStep(step)) continue;
		if (step.type === 'system') {
			const setupGroup: TrajectoryTraceGroup = {
				id: step.message_id || step.event_id || step.step_id || `setup:${groups.length}`,
				label: 'Setup',
				messages: [step],
				evidence: [],
				items: [{ kind: 'message', step }]
			};
			groups.push(setupGroup);
			registerMessage(setupGroup, step);
			currentExchange = null;
			continue;
		}

		const actor = trajectoryActorKey(step);
		const startsExchange =
			currentExchange === null ||
			actor === 'auditor' ||
			actor === 'tester' ||
			actor === 'user' ||
			step.role === 'user';
		let exchange: TrajectoryTraceGroup | null = currentExchange;
		if (startsExchange || exchange === null) {
			exchangeIndex += 1;
			exchange = {
				id: step.message_id || step.event_id || step.step_id || `exchange:${exchangeIndex}`,
				label: `Exchange ${exchangeIndex}`,
				messages: [],
				evidence: [],
				items: []
			};
			currentExchange = exchange;
			groups.push(exchange);
		}
		exchange.messages.push(step);
		exchange.items.push({ kind: 'message', step });
		registerMessage(exchange, step);
	}

	const ungroupedEvidence: TrajectoryStep[] = [];
	for (const step of steps) {
		if (isTranscriptMessageStep(step)) continue;
		const group = step.message_id ? messageGroups.get(step.message_id) : undefined;
		if (group) addEvidenceToGroup(group, step, step.message_id);
		else ungroupedEvidence.push(step);
	}

	const singleExchange = singleMessageExchange(groups);
	if (singleExchange) {
		for (const step of ungroupedEvidence) {
			addEvidenceToGroup(singleExchange, step, assistantMessageId(singleExchange));
		}
		return groups;
	}

	if (ungroupedEvidence.length) {
		const ordered = [...ungroupedEvidence].sort(compareTrajectorySteps);
		groups.push({
			id: `runtime-evidence:${groups.length}`,
			label: 'Runtime evidence',
			messages: [],
			evidence: ordered,
			items: ordered.map((step) => ({ kind: 'evidence', step }))
		});
	}
	return groups;
}

export function buildInlineRuntimeEvidenceByMessageId(groups: TrajectoryTraceGroup[]): Map<string, TrajectoryStep[]> {
	const result = new Map<string, TrajectoryStep[]>();
	for (const group of groups) {
		let pendingEvidence: TrajectoryStep[] = [];
		for (const item of group.items) {
			if (item.kind === 'evidence') {
				pendingEvidence.push(item.step);
				continue;
			}
			const key = messageKey(item.step);
			if (key && pendingEvidence.length) {
				const merged = [...(result.get(key) ?? []), ...pendingEvidence];
				merged.sort(compareTrajectorySteps);
				result.set(key, merged);
			}
			pendingEvidence = [];
		}
	}
	return result;
}

function addEvidenceToGroup(group: TrajectoryTraceGroup, step: TrajectoryStep, beforeMessageId?: string | null): void {
	group.evidence.push(step);
	const insertionIndex = beforeMessageId
		? group.items.findIndex((item) => item.kind === 'message' && messageKey(item.step) === beforeMessageId)
		: -1;
	if (insertionIndex >= 0) {
		group.items.splice(insertionIndex, 0, { kind: 'evidence', step });
		return;
	}
	group.items.push({ kind: 'evidence', step });
}

function messageKey(step: TrajectoryStep): string | undefined {
	return step.message_id || step.event_id || undefined;
}

function compareTrajectorySteps(a: TrajectoryStep, b: TrajectoryStep): number {
	// Use the monotonic capture-time `index` field. Falls back to step_id parsing
	// (e.g. "step:7") so the ordering is still stable when index is missing.
	const ai = typeof a.index === 'number' ? a.index : parseStepIdNumber(a.step_id);
	const bi = typeof b.index === 'number' ? b.index : parseStepIdNumber(b.step_id);
	if (ai !== bi) return ai - bi;
	return 0;
}

function parseStepIdNumber(stepId: string | null | undefined): number {
	if (!stepId) return Number.MAX_SAFE_INTEGER;
	const match = /(\d+)/.exec(stepId);
	return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function singleMessageExchange(groups: TrajectoryTraceGroup[]): TrajectoryTraceGroup | null {
	const exchanges = groups.filter((group) => group.messages.some((step) => step.type === 'message'));
	return exchanges.length === 1 ? exchanges[0] : null;
}

function assistantMessageId(group: TrajectoryTraceGroup): string | undefined {
	const assistant = group.messages.find(
		(step) => step.role === 'assistant' || (trajectoryActorKey(step) === 'target' && step.role !== 'user')
	);
	return assistant ? messageKey(assistant) : undefined;
}
