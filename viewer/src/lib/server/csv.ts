// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

/**
 * CSV serialization helpers — RFC 4180 compliant, zero dependencies.
 */

import type { AuditTranscript, InteractionMessage } from '$lib/types.js';

const FORMULA_PREFIXES = new Set(['=', '+', '-', '@', '\t', '|']);

/** Escape a single cell value for CSV. */
export function escapeCell(value: unknown): string {
	if (value == null) return '';
	let str = String(value);
	// Neutralize CSV formula injection
	if (str.length > 0 && FORMULA_PREFIXES.has(str[0])) {
		str = "'" + str;
	}
	if (str.includes('"') || str.includes(',') || str.includes('\n') || str.includes('\r')) {
		return `"${str.replace(/"/g, '""')}"`;
	}
	return str;
}

/** Serialize rows into a CSV string with a header row and UTF-8 BOM. */
export function toCsv(columns: string[], rows: Record<string, unknown>[]): string {
	const header = columns.map(escapeCell).join(',');
	const body = rows.map((row) => columns.map((col) => escapeCell(row[col])).join(',')).join('\n');
	return '\uFEFF' + header + '\n' + body + '\n';
}

/** Sanitize a filename component by stripping control characters and path separators. */
function sanitizeFilename(name: string): string {
	return name.replace(/[^\w.@()-]/g, '_');
}

/** Build a Response with CSV content-disposition headers. */
export function csvResponse(filename: string, columns: string[], rows: Record<string, unknown>[]): Response {
	const csv = toCsv(columns, rows);
	const safeName = sanitizeFilename(filename);
	return new Response(csv, {
		headers: {
			'Content-Type': 'text/csv; charset=utf-8',
			'Content-Disposition': `attachment; filename="${safeName}"`,
			'Content-Length': String(new TextEncoder().encode(csv).byteLength)
		}
	});
}

/** Collect the judge dimension names (boolean flags under verdict.dimensions). */
export function detectJudgeDimensions(
	verdicts: Array<Record<string, unknown> | null | undefined>
): string[] {
	const dims = new Set<string>();
	for (const verdict of verdicts) {
		const dimensions = verdict?.dimensions;
		if (dimensions && typeof dimensions === 'object' && !Array.isArray(dimensions)) {
			for (const [key, value] of Object.entries(dimensions)) {
				if (typeof value === 'boolean') dims.add(key);
			}
		}
	}
	return [...dims].sort((left, right) => {
		if (left === 'policy_violation') return -1;
		if (right === 'policy_violation') return 1;
		return left.localeCompare(right);
	});
}

/** Collect the stratification dimension names from each row's `dimensions` map. */
export function detectStratificationDimensions(
	items: Array<{ dimensions?: Record<string, string> }>
): string[] {
	const names = new Set<string>();
	for (const item of items) {
		for (const key of Object.keys(item.dimensions ?? {})) names.add(key);
	}
	return [...names].sort();
}

/** Read a judge dimension value from a verdict. */
export function judgeDimensionValue(
	verdict: Record<string, unknown> | null | undefined,
	dim: string
): unknown {
	const dimensions = verdict?.dimensions;
	if (dimensions && typeof dimensions === 'object' && !Array.isArray(dimensions) && dim in dimensions) {
		return (dimensions as Record<string, unknown>)[dim];
	}
	return verdict?.[dim] ?? '';
}

/** Extract the judge narrative/justification text from a verdict. */
export function verdictJustification(verdict: Record<string, unknown> | null | undefined): string {
	if (typeof verdict?.narrative === 'string' && verdict.narrative) return verdict.narrative;
	if (typeof verdict?.justification === 'string') return verdict.justification;
	return '';
}

/** Format a prompt-result messages array into a readable conversation string. */
export function formatConversation(messages: InteractionMessage[] | undefined): string {
	if (!messages?.length) return '';
	return messages
		.map((message) => {
			const role = message.role.toUpperCase();
			if (message.tool_calls?.length) {
				const calls = message.tool_calls
					.map((call) => `${call.function}(${JSON.stringify(call.arguments)})`)
					.join('; ');
				return `[${role}]: ${message.content || ''}\n  tool_calls: ${calls}`;
			}
			if (message.role === 'tool') {
				return `[TOOL ${message.function ?? ''}]: ${message.content}`;
			}
			return `[${role}]: ${message.content}`;
		})
		.join('\n');
}

/** Format a scenario inference transcript into a readable conversation string. */
export function formatAuditTranscript(transcript: AuditTranscript | undefined): string {
	if (!transcript?.events?.length) return '';
	return transcript.events
		.map((event) => {
			const edit = event.edit;
			if (!edit) return null;
			if ((edit.type === 'add_message' || edit.type === 'set_system_message') && edit.message) {
				return `[${edit.message.role.toUpperCase()}]: ${edit.message.content}`;
			}
			if (edit.type === 'tool_call' && edit.tool_name) {
				const args = edit.tool_args ? JSON.stringify(edit.tool_args) : '{}';
				return `[TOOL ${edit.tool_name}]: (${args}) → ${edit.tool_result || ''}`;
			}
			return null;
		})
		.filter(Boolean)
		.join('\n');
}
