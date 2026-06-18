"use client";

import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import type { DocMeta, DocSearchEntry } from "../_lib/docs";

type NavGroup = { group: string | null; items: DocMeta[] };

type SearchResult = {
	href: string;
	title: string;
	group: string | null;
	snippet: ReactNode;
};

const SNIPPET_BEFORE = 40;
const SNIPPET_AFTER = 120;

function escapeRegExp(value: string): string {
	return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// Wrap occurrences of the query within text in <mark> for highlighting.
function highlight(text: string, query: string): ReactNode {
	if (!query) return text;
	const re = new RegExp(escapeRegExp(query), "gi");
	const parts: ReactNode[] = [];
	let lastIndex = 0;
	let match: RegExpExecArray | null;
	let key = 0;
	while ((match = re.exec(text)) !== null) {
		if (match.index > lastIndex) parts.push(text.slice(lastIndex, match.index));
		parts.push(
			<mark key={key++} className="docs-search-hl">
				{match[0]}
			</mark>,
		);
		lastIndex = match.index + match[0].length;
		if (match.index === re.lastIndex) re.lastIndex++;
	}
	if (lastIndex < text.length) parts.push(text.slice(lastIndex));
	return parts;
}

function buildResult(entry: DocSearchEntry, q: string): SearchResult | null {
	const haystack = entry.text;
	const lower = haystack.toLowerCase();
	const titleMatch = entry.title.toLowerCase().includes(q);
	const idx = lower.indexOf(q);

	// No match anywhere in title or body -> not a result.
	if (idx === -1 && !titleMatch) return null;

	// Deep-link to the nearest heading preceding the body match.
	let href = entry.href;
	let snippetText = "";
	if (idx !== -1) {
		let heading = null as (typeof entry.headings)[number] | null;
		for (const h of entry.headings) {
			if (h.offset <= idx) heading = h;
			else break;
		}
		if (heading) href = `${entry.href}#${heading.id}`;

		const start = Math.max(0, idx - SNIPPET_BEFORE);
		const end = Math.min(haystack.length, idx + q.length + SNIPPET_AFTER);
		snippetText =
			(start > 0 ? "\u2026" : "") +
			haystack.slice(start, end).trim() +
			(end < haystack.length ? "\u2026" : "");
	} else {
		// Title-only match: show the opening of the body as context.
		snippetText = haystack.slice(0, SNIPPET_AFTER).trim() + (haystack.length > SNIPPET_AFTER ? "\u2026" : "");
	}

	return {
		href,
		title: entry.title,
		group: entry.group,
		snippet: highlight(snippetText, q),
	};
}

export default function DocsSidebar({
	nav,
	searchIndex,
	activeHref,
}: {
	nav: NavGroup[];
	searchIndex: DocSearchEntry[];
	activeHref?: string;
}) {
	const [query, setQuery] = useState("");

	const q = query.trim().toLowerCase();

	const results = useMemo<SearchResult[]>(() => {
		if (!q) return [];
		const out: SearchResult[] = [];
		for (const entry of searchIndex) {
			const r = buildResult(entry, q);
			if (r) out.push(r);
		}
		return out;
	}, [searchIndex, q]);

	return (
		<aside className="docs-sidebar">
			<div className="docs-sidebar-inner">
				<div className="docs-search">
					<svg
						className="docs-search-icon"
						width="14"
						height="14"
						viewBox="0 0 16 16"
						fill="none"
						aria-hidden="true"
					>
						<path
							d="M11.5 10.5L14 13M6.75 11.5C9.37 11.5 11.5 9.37 11.5 6.75C11.5 4.13 9.37 2 6.75 2C4.13 2 2 4.13 2 6.75C2 9.37 4.13 11.5 6.75 11.5Z"
							stroke="currentColor"
							strokeWidth="1.4"
							strokeLinecap="round"
						/>
					</svg>
					<input
						type="search"
						className="docs-search-input"
						placeholder="Search docs"
						value={query}
						onChange={(e) => setQuery(e.target.value)}
						aria-label="Search documentation"
					/>
				</div>

				{q ? (
					results.length > 0 ? (
						<ul className="docs-search-results">
							{results.map((r, i) => (
								<li key={`${r.href}-${i}`}>
									<Link href={r.href} className="docs-search-result">
										<span className="docs-search-result-title">
											{r.group && (
												<span className="docs-search-result-group">{r.group} · </span>
											)}
											{r.title}
										</span>
										<span className="docs-search-result-snippet">{r.snippet}</span>
									</Link>
								</li>
							))}
						</ul>
					) : (
						<p className="docs-search-empty">No matches.</p>
					)
				) : (
					nav.map((group, gi) => (
						<div key={group.group ?? `g-${gi}`} className="docs-sidebar-group">
							{group.group && (
								<div className="docs-sidebar-group-title">{group.group}</div>
							)}
							<ul className="docs-sidebar-list">
								{/* Place the docs index link at the top of the first untitled
								    group so it shares the same row rhythm as the other top-level items. */}
								{gi === 0 && !group.group && (
									<li>
										<Link
											href="/docs"
											className={`docs-sidebar-link${
												activeHref === "/docs" ? " is-active" : ""
											}`}
										>
											ASSERT Documentation
										</Link>
									</li>
								)}
								{group.items.map((item) => (
									<li key={item.href}>
										<Link
											href={item.href}
											className={`docs-sidebar-link${
												activeHref === item.href ? " is-active" : ""
											}`}
										>
											{item.title}
										</Link>
									</li>
								))}
							</ul>
						</div>
					))
				)}
			</div>
		</aside>
	);
}
