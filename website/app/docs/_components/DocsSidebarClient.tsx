"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { DocMeta } from "../_lib/docs";

type NavGroup = { group: string | null; items: DocMeta[] };

export default function DocsSidebar({
	nav,
	activeHref,
}: {
	nav: NavGroup[];
	activeHref?: string;
}) {
	const [query, setQuery] = useState("");

	const filtered = useMemo<NavGroup[]>(() => {
		const q = query.trim().toLowerCase();
		if (!q) return nav;
		return nav
			.map((g) => ({
				group: g.group,
				items: g.items.filter(
					(item) =>
						item.title.toLowerCase().includes(q) ||
						(item.description ?? "").toLowerCase().includes(q),
				),
			}))
			.filter((g) => g.items.length > 0);
	}, [nav, query]);

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

				{filtered.map((group, gi) => (
					<div key={group.group ?? `g-${gi}`} className="docs-sidebar-group">
						{group.group && (
							<div className="docs-sidebar-group-title">{group.group}</div>
						)}
						<ul className="docs-sidebar-list">
							{/* Place Overview at the top of the first untitled group so it
							    shares the same row rhythm as the other top-level items. */}
							{gi === 0 && !group.group && !query.trim() && (
								<li>
									<Link
										href="/docs"
										className={`docs-sidebar-link${
											activeHref === "/docs" ? " is-active" : ""
										}`}
									>
										Overview
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
				))}

				{filtered.length === 0 && (
					<p className="docs-search-empty">No matches.</p>
				)}
			</div>
		</aside>
	);
}
