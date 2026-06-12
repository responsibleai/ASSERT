"use client";

import { useEffect, useState } from "react";

const SECTIONS = [
	{ id: "overview", label: "Overview" },
	{ id: "evaluation-spec", label: "Evaluation Spec" },
	{ id: "how-it-works", label: "How it works" },
	{ id: "systematization", label: "Systematization" },
	{ id: "resources", label: "Resources" },
];

export default function OnThisPageNav() {
	const [activeId, setActiveId] = useState<string>(SECTIONS[0].id);
	const [visible, setVisible] = useState(false);

	useEffect(() => {
		const els = SECTIONS
			.map((s) => document.getElementById(s.id))
			.filter((el): el is HTMLElement => el !== null);
		const firstEl = els[0];

		const update = () => {
			const probe = window.innerHeight * 0.35;
			let current = SECTIONS[0].id;
			for (const el of els) {
				if (el.getBoundingClientRect().top - probe <= 0) {
					current = el.id;
				}
			}

			// If scrolled to (near) the bottom of the page, activate the last
			// tracked section even when its top hasn't reached the probe line.
			const scrollBottom = window.scrollY + window.innerHeight;
			const docHeight = document.documentElement.scrollHeight;
			if (scrollBottom >= docHeight - 4 && els.length > 0) {
				current = els[els.length - 1].id;
			}

			setActiveId(current);

			// Show once user has scrolled to (or past) the first tracked section.
			if (firstEl) {
				setVisible(firstEl.getBoundingClientRect().top <= window.innerHeight * 0.6);
			}
		};

		update();
		window.addEventListener("scroll", update, { passive: true });
		window.addEventListener("resize", update);
		return () => {
			window.removeEventListener("scroll", update);
			window.removeEventListener("resize", update);
		};
	}, []);

	return (
		<aside
			className={`on-this-page${visible ? " is-visible" : ""}`}
			aria-label="On this page"
		>
			<div className="on-this-page-title">ON THIS PAGE</div>
			<ul className="on-this-page-list">
				{SECTIONS.map((s) => {
					const active = s.id === activeId;
					return (
						<li key={s.id} className={`on-this-page-item${active ? " is-active" : ""}`}>
							<a href={`#${s.id}`}>
								<span className="on-this-page-bar" aria-hidden="true" />
								<span className="on-this-page-label">{s.label}</span>
							</a>
						</li>
					);
				})}
			</ul>
		</aside>
	);
}
