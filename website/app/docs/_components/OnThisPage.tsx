"use client";

import { useEffect, useState } from "react";
import type { Heading } from "../_lib/docs";

export default function OnThisPage({ headings }: { headings: Heading[] }) {
	const [activeId, setActiveId] = useState<string>(headings[0]?.id ?? "");

	useEffect(() => {
		if (headings.length === 0) return;
		const els = headings
			.map((h) => document.getElementById(h.id))
			.filter((el): el is HTMLElement => el !== null);
		if (els.length === 0) return;

		const update = () => {
			const probe = window.innerHeight * 0.2;
			let current = els[0].id;
			for (const el of els) {
				if (el.getBoundingClientRect().top - probe <= 0) {
					current = el.id;
				}
			}
			const scrollBottom = window.scrollY + window.innerHeight;
			if (scrollBottom >= document.documentElement.scrollHeight - 4) {
				current = els[els.length - 1].id;
			}
			setActiveId(current);
		};

		update();
		window.addEventListener("scroll", update, { passive: true });
		window.addEventListener("resize", update);
		return () => {
			window.removeEventListener("scroll", update);
			window.removeEventListener("resize", update);
		};
	}, [headings]);

	if (headings.length === 0) return null;

	return (
		<aside className="on-this-page on-this-page--docs is-visible" aria-label="On this page">
			<div className="on-this-page-title">ON THIS PAGE</div>
			<ul className="on-this-page-list">
				{headings.map((h) => {
					const active = h.id === activeId;
					return (
						<li
							key={h.id}
							className={`on-this-page-item on-this-page-item--level-${h.level}${
								active ? " is-active" : ""
							}`}
						>
							<a href={`#${h.id}`}>
								<span className="on-this-page-bar" aria-hidden="true" />
								<span className="on-this-page-label">{h.text}</span>
							</a>
						</li>
					);
				})}
			</ul>
		</aside>
	);
}
