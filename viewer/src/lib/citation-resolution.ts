// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

export interface CitationAnchorLike {
	exact?: string | null;
	prefix?: string | null;
	suffix?: string | null;
	hint?: number | null;
}

export type CitationSourceKind = 'message' | 'tool_arg' | 'tool_result';

export interface CitationPartLike {
	matched_message_index?: string | null;
	message_id?: string | null;
	source_kind?: CitationSourceKind | null;
	tool_call_id?: string | null;
	tool_arg?: string | null;
	quoted_text?: string | null;
	position?: [number, number] | null;
	anchor?: CitationAnchorLike | null;
	resolution?: {
		status?: CitationResolutionStatus | null;
		method?: CitationResolutionReason | null;
		detail?: string | null;
	} | null;
}

export type CitationResolutionStatus = 'resolved' | 'unresolved' | 'ambiguous';

export type CitationResolutionReason =
	| 'exact'
	| 'invalid_position'
	| 'missing_message_id'
	| 'missing_message_text'
	| 'missing_resolution'
	| 'quote_not_found'
	| 'ambiguous_quote_match'
	| 'conservative_fuzzy';

export interface CitationResolution {
	resolved: boolean;
	status: CitationResolutionStatus;
	reason: CitationResolutionReason;
	position: [number, number] | null;
	detail?: string;
	source: 'stored' | 'computed';
}

export interface CitationDisplayRange {
	start: number;
	end: number;
}

export interface CitationReference {
	indices: number[];
	startPos: number;
	endPos: number;
	originalText: string;
}

const NORMALIZED_QUOTES = new Map<string, string>([
	['\u2018', "'"],
	['\u2019', "'"],
	['\u201c', '"'],
	['\u201d', '"']
]);

interface TextIndex {
	text: string;
	rawByNormalized: number[];
}

function isWordBoundary(char: string | undefined): boolean {
	return !char || !/[\p{L}\p{N}]/u.test(char);
}

function isMarkdownFormattingMarker(text: string, index: number): boolean {
	const char = text[index];
	if (char === '`') return true;
	if (char !== '*' && char !== '_') return false;
	const prevChar = index > 0 ? text[index - 1] : '';
	const nextChar = index + 1 < text.length ? text[index + 1] : '';
	if (prevChar === char || nextChar === char) return true;
	return isWordBoundary(prevChar) !== isWordBoundary(nextChar);
}

function normalizeTextWithOffsetMap(rawText: string, { stripMarkdown }: { stripMarkdown: boolean }): TextIndex {
	const normalizedChars: string[] = [];
	const rawByNormalized: number[] = [];
	let pendingSpaceIndex: number | null = null;

	for (let index = 0; index < rawText.length; index += 1) {
		const rawChar = rawText[index];
		if (stripMarkdown && isMarkdownFormattingMarker(rawText, index)) {
			continue;
		}
		const char = NORMALIZED_QUOTES.get(rawChar) ?? rawChar;
		if (/\s/u.test(char)) {
			if (normalizedChars.length > 0 && pendingSpaceIndex === null) {
				pendingSpaceIndex = index;
			}
			continue;
		}
		if (pendingSpaceIndex !== null) {
			normalizedChars.push(' ');
			rawByNormalized.push(pendingSpaceIndex);
			pendingSpaceIndex = null;
		}
		normalizedChars.push(char);
		rawByNormalized.push(index);
	}

	return {
		text: normalizedChars.join(''),
		rawByNormalized
	};
}

function isValidPosition(position: [number, number] | null | undefined, messageText: string): position is [number, number] {
	return Boolean(
		position &&
		Number.isInteger(position[0]) &&
		Number.isInteger(position[1]) &&
		position[0] >= 0 &&
		position[1] > position[0] &&
		position[1] <= messageText.length
	);
}

function buildResult(
	status: CitationResolutionStatus,
	reason: CitationResolutionReason,
	position: [number, number] | null,
	source: 'stored' | 'computed',
	detail?: string | null
): CitationResolution {
	return {
		resolved: status === 'resolved' && position !== null,
		status,
		reason,
		position: status === 'resolved' ? position : null,
		detail: detail ?? undefined,
		source
	};
}

export function resolveCitationPart(
	part: CitationPartLike,
	messageText: string | null | undefined
): CitationResolution {
	if (part.resolution?.method) {
		const status = part.resolution.status ?? 'unresolved';
		if (status === 'resolved') {
			if (typeof messageText === 'string' && isValidPosition(part.position, messageText)) {
				return buildResult('resolved', part.resolution.method, part.position, 'stored', part.resolution.detail);
			}
			return buildResult(
				'unresolved',
				'invalid_position',
				null,
				'stored',
				'Stored citation span is no longer valid for the current message text. Re-run judge for canonical evidence.'
			);
		}
		return buildResult(status, part.resolution.method, null, 'stored', part.resolution.detail);
	}
	if (!part.message_id) {
		return buildResult('unresolved', 'missing_message_id', null, 'stored', 'Citation did not reference a transcript message.');
	}
	if (typeof messageText !== 'string' || !messageText) {
		return buildResult('unresolved', 'missing_message_text', null, 'stored', 'Transcript message text was empty or unavailable.');
	}
	return buildResult('unresolved', 'missing_resolution', null, 'stored', 'Citation resolution metadata is missing. Re-run judge for canonical evidence.');
}

export function getResolvedCitationPartsForMessage<T extends CitationPartLike>(
	parts: T[],
	messageText: string | null | undefined
): T[] {
	return parts.filter((part) => resolveCitationPart(part, messageText).resolved);
}

export function getCitationRawRanges(
	messageText: string,
	parts: CitationPartLike[]
): CitationDisplayRange[] {
	if (!messageText || parts.length === 0) return [];

	const ranges: CitationDisplayRange[] = [];
	for (const part of parts) {
		const resolution = resolveCitationPart(part, messageText);
		if (!resolution.resolved || !resolution.position) continue;
		ranges.push({ start: resolution.position[0], end: resolution.position[1] });
	}
	return mergeDisplayRanges(ranges);
}

export function getCitationDisplayRanges(
	messageText: string,
	parts: CitationPartLike[]
): CitationDisplayRange[] {
	if (!messageText || parts.length === 0) return [];

	const displayIndex = normalizeTextWithOffsetMap(messageText, { stripMarkdown: true });
	const ranges: CitationDisplayRange[] = [];

	for (const part of parts) {
		const resolution = resolveCitationPart(part, messageText);
		if (!resolution.resolved || !resolution.position) continue;
		const displayRange = rawSpanToDisplayRange(displayIndex, resolution.position);
		if (!displayRange) continue;
		ranges.push(displayRange);
	}

	return mergeDisplayRanges(ranges);
}

function rawSpanToDisplayRange(
	displayIndex: TextIndex,
	position: [number, number]
): CitationDisplayRange | null {
	if (displayIndex.rawByNormalized.length === 0) return null;
	const [rawStart, rawEnd] = position;
	let displayStart = -1;
	let displayEnd = -1;
	for (let index = 0; index < displayIndex.rawByNormalized.length; index += 1) {
		const rawOffset = displayIndex.rawByNormalized[index];
		if (displayStart < 0 && rawOffset >= rawStart) {
			displayStart = index;
		}
		if (rawOffset < rawEnd) {
			displayEnd = index + 1;
		}
	}
	if (displayStart < 0 || displayEnd <= displayStart) return null;
	return {
		start: displayStart,
		end: displayEnd
	};
}

function mergeDisplayRanges(ranges: CitationDisplayRange[]): CitationDisplayRange[] {
	if (ranges.length === 0) return [];
	const sorted = [...ranges].sort((left, right) => left.start - right.start || left.end - right.end);
	const merged: CitationDisplayRange[] = [sorted[0]];
	for (const current of sorted.slice(1)) {
		const last = merged[merged.length - 1];
		if (current.start <= last.end) {
			last.end = Math.max(last.end, current.end);
			continue;
		}
		merged.push({ ...current });
	}
	return merged;
}

function parseCitationIndices(raw: string): number[] {
	const indices: number[] = [];
	const parts = raw
		.split(',')
		.map((part) => part.trim())
		.filter(Boolean);

	for (const part of parts) {
		if (part.includes('-')) {
			const [left, right] = part.split('-').map((value) => Number.parseInt(value.trim(), 10));
			if (Number.isInteger(left) && Number.isInteger(right) && left > 0 && right >= left) {
				for (let value = left; value <= right; value += 1) {
					indices.push(value);
				}
			}
			continue;
		}
		const value = Number.parseInt(part, 10);
		if (Number.isInteger(value) && value > 0) {
			indices.push(value);
		}
	}

	return [...new Set(indices)].sort((left, right) => left - right);
}

export function parseCitationReferences(text: string): CitationReference[] {
	const references: CitationReference[] = [];
	const regex = /\[([0-9,\s-]+)\]/g;
	let match: RegExpExecArray | null = regex.exec(text);

	while (match) {
		const indices = parseCitationIndices(match[1] ?? '');
		if (indices.length > 0) {
			references.push({
				indices,
				startPos: match.index,
				endPos: match.index + match[0].length,
				originalText: match[0]
			});
		}
		match = regex.exec(text);
	}

	return references;
}
