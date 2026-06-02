/**
 * CSV serialization helpers — RFC 4180 compliant, zero dependencies.
 */

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
