// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

import { marked, type RendererThis, type Tokens } from 'marked';
import type { CitationDisplayRange } from '$lib/citation-resolution.js';

const ESCAPE_LOOKUP: Record<string, string> = {
	'&': '&amp;',
	'<': '&lt;',
	'>': '&gt;',
	'"': '&quot;',
	"'": '&#39;'
};

const ESCAPE_REGEX = /[&<>"']/g;
const SAFE_PROTOCOLS = new Set(['http:', 'https:', 'mailto:', 'tel:']);
let highlightPlaceholderCounter = 0;

function escapeHtml(value: string): string {
	return value.replace(ESCAPE_REGEX, char => ESCAPE_LOOKUP[char]);
}

function normalizeVisibleTextWithOffsetMap(text: string): { text: string; rawByNormalized: number[] } {
	const normalizedChars: string[] = [];
	const rawByNormalized: number[] = [];
	let pendingSpaceIndex: number | null = null;

	for (let index = 0; index < text.length; index += 1) {
		const char = text[index];
		const normalizedChar =
			char === '\u2018' || char === '\u2019'
				? "'"
				: char === '\u201c' || char === '\u201d'
					? '"'
					: char;
		if (/\s/u.test(normalizedChar)) {
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
		normalizedChars.push(normalizedChar);
		rawByNormalized.push(index);
	}

	return {
		text: normalizedChars.join(''),
		rawByNormalized
	};
}

function renderHighlightedTextSegment(
	text: string,
	ranges: CitationDisplayRange[],
	state: { offset: number }
): string {
	if (!text || ranges.length === 0) return escapeHtml(text);

	const normalized = normalizeVisibleTextWithOffsetMap(text);
	if (!normalized.text) return escapeHtml(text);

	const segmentStart = state.offset;
	const segmentEnd = segmentStart + normalized.text.length;
	const overlapping = ranges.filter((range) => range.end > segmentStart && range.start < segmentEnd);
	state.offset = segmentEnd;
	if (overlapping.length === 0) return escapeHtml(text);

	const localRanges = overlapping.map((range) => ({
		start: Math.max(0, range.start - segmentStart),
		end: Math.min(normalized.text.length, range.end - segmentStart)
	}));

	let html = '';
	let rawCursor = 0;
	for (const range of localRanges) {
		const rawStart = normalized.rawByNormalized[range.start];
		const rawEnd = normalized.rawByNormalized[range.end - 1] + 1;
		if (rawStart > rawCursor) {
			html += escapeHtml(text.slice(rawCursor, rawStart));
		}
		html += `<mark class="citation-hl">${escapeHtml(text.slice(rawStart, rawEnd))}</mark>`;
		rawCursor = rawEnd;
	}
	if (rawCursor < text.length) {
		html += escapeHtml(text.slice(rawCursor));
	}
	return html;
}

function sanitizeUrl(url: string | null | undefined): string | null {
	if (!url) return null;
	const trimmed = url.trim();
	if (!trimmed) return null;
	if (/[\u0000-\u001F\u007F\s]/.test(trimmed)) return null;
	if (trimmed.startsWith('#') || trimmed.startsWith('/') || trimmed.startsWith('./') || trimmed.startsWith('../')) {
		try {
			return encodeURI(trimmed);
		} catch {
			return null;
		}
	}
	if (trimmed.startsWith('//')) return null;
	const schemeMatch = /^[a-zA-Z][a-zA-Z0-9+.-]*:/.exec(trimmed);
	if (schemeMatch) {
		const scheme = schemeMatch[0].toLowerCase();
		if (!SAFE_PROTOCOLS.has(scheme)) return null;
	}
	try {
		return encodeURI(trimmed);
	} catch {
		return null;
	}
}

const SAFE_RENDERER = new marked.Renderer();
SAFE_RENDERER.html = () => '';
SAFE_RENDERER.link = function (this: RendererThis, { href, title, tokens }: Tokens.Link) {
	const text = this.parser.parseInline(tokens);
	const safeHref = sanitizeUrl(href);
	if (!safeHref) return text;
	const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
	return `<a href="${escapeHtml(safeHref)}"${titleAttr} rel="nofollow noreferrer noopener">${text}</a>`;
};
SAFE_RENDERER.image = function (this: RendererThis, { href, title, text }: Tokens.Image) {
	const safeSrc = sanitizeUrl(href);
	if (!safeSrc) return text ? escapeHtml(text) : '';
	const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
	const alt = escapeHtml(text ?? '');
	return `<img src="${escapeHtml(safeSrc)}" alt="${alt}"${titleAttr}>`;
};

export function renderMarkdown(text: string, { breaks = true }: { breaks?: boolean } = {}): string {
	if (!text) return '';
	return marked.parse(text, { breaks, renderer: SAFE_RENDERER }) as string;
}

function isMarkdownSyntaxBoundary(text: string, index: number): boolean {
	const char = text[index];
	return char === '*' || char === '_' || char === '`' || char === '[' || char === ']' || char === '(' || char === ')';
}

function splitRangeByMarkdownSyntax(text: string, start: number, end: number): CitationDisplayRange[] {
	const segments: CitationDisplayRange[] = [];
	let cursor = start;
	while (cursor < end) {
		while (cursor < end && isMarkdownSyntaxBoundary(text, cursor)) cursor += 1;
		const segmentStart = cursor;
		while (cursor < end && !isMarkdownSyntaxBoundary(text, cursor)) cursor += 1;
		if (cursor > segmentStart) {
			segments.push({ start: segmentStart, end: cursor });
		}
	}
	return segments;
}

function injectHighlightPlaceholders(text: string, ranges: CitationDisplayRange[]): { source: string; count: number; nonce: number } {
	const sorted = [...ranges]
		.filter((range) => range.end > range.start)
		.sort((left, right) => left.start - right.start || left.end - right.end);

	let result = '';
	let cursor = 0;
	let insertedCount = 0;
	highlightPlaceholderCounter += 1;
	const nonce = highlightPlaceholderCounter;

	for (const range of sorted) {
		const rangeStart = Math.max(cursor, range.start);
		const rangeEnd = Math.min(text.length, range.end);
		if (rangeEnd <= rangeStart) continue;

		const segments = splitRangeByMarkdownSyntax(text, rangeStart, rangeEnd);
		for (const segment of segments) {
			if (segment.start > cursor) {
				result += text.slice(cursor, segment.start);
			}
			const startToken = `@@ASSERT_CIT_HL_S_${nonce}_${insertedCount}@@`;
			const endToken = `@@ASSERT_CIT_HL_E_${nonce}_${insertedCount}@@`;
			result += startToken + text.slice(segment.start, segment.end) + endToken;
			cursor = segment.end;
			insertedCount += 1;
		}
	}

	if (cursor < text.length) {
		result += text.slice(cursor);
	}
	return { source: result, count: insertedCount, nonce };
}

function replaceHighlightPlaceholders(html: string, count: number, nonce: number): string {
	let highlighted = html;
	for (let index = 0; index < count; index += 1) {
		highlighted = highlighted
			.split(`@@ASSERT_CIT_HL_S_${nonce}_${index}@@`)
			.join('<mark class="citation-hl">')
			.split(`@@ASSERT_CIT_HL_E_${nonce}_${index}@@`)
			.join('</mark>');
	}
	return highlighted;
}

export function renderMarkdownWithRawHighlights(
	text: string,
	ranges: CitationDisplayRange[],
	{ breaks = true }: { breaks?: boolean } = {}
): string {
	if (!text) return '';
	if (ranges.length === 0) return renderMarkdown(text, { breaks });
	const injected = injectHighlightPlaceholders(text, ranges);
	const html = marked.parse(injected.source, { breaks, renderer: SAFE_RENDERER }) as string;
	return replaceHighlightPlaceholders(html, injected.count, injected.nonce);
}

export function renderMarkdownWithHighlights(
	text: string,
	ranges: CitationDisplayRange[],
	{ breaks = true }: { breaks?: boolean } = {}
): string {
	if (!text) return '';
	if (ranges.length === 0) return renderMarkdown(text, { breaks });

	const highlightState = { offset: 0 };
	const renderer = new marked.Renderer();
	renderer.html = SAFE_RENDERER.html;
	renderer.link = SAFE_RENDERER.link;
	renderer.image = SAFE_RENDERER.image;
	renderer.text = function (this: RendererThis, token: Tokens.Text | Tokens.Escape) {
		if ('tokens' in token && Array.isArray(token.tokens) && token.tokens.length > 0) {
			return this.parser.parseInline(token.tokens);
		}
		return renderHighlightedTextSegment(token.text, ranges, highlightState);
	};
	renderer.codespan = function ({ text: segmentText }: Tokens.Codespan) {
		return `<code>${renderHighlightedTextSegment(segmentText, ranges, highlightState)}</code>`;
	};

	return marked.parse(text, { breaks, renderer }) as string;
}
