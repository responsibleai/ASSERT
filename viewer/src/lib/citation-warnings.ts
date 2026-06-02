// Copyright (c) Microsoft Corporation.
// Licensed under the MIT License.

const NON_DEGRADED_WARNING_REASONS = new Set(['overscoped_citation_part']);

const WARNING_LABELS: Record<string, string> = {
	ambiguous_citation_part: 'ambiguous citation match',
	invalid_citation_message_id: 'invalid message reference',
	missing_citations: 'missing citations',
	noncanonical_citation_method: 'noncanonical citation match',
	unresolved_citation_part: 'unresolved citation match'
};

export function citationWarningReason(warning: string): string {
	const separatorIndex = warning.indexOf(':');
	return separatorIndex >= 0 ? warning.slice(separatorIndex + 1) : warning;
}

export function isDegradedCitationWarning(warning: string): boolean {
	return !NON_DEGRADED_WARNING_REASONS.has(citationWarningReason(warning));
}

export function citationWarningLabel(warning: string): string {
	const separatorIndex = warning.indexOf(':');
	const prefix = separatorIndex >= 0 ? warning.slice(0, separatorIndex) : '';
	const reason = citationWarningReason(warning);
	const citationMatch = /^citation_(\d+)$/.exec(prefix);
	const label = WARNING_LABELS[reason] ?? reason.replace(/_/g, ' ');
	return citationMatch ? `citation ${citationMatch[1]}: ${label}` : label;
}
