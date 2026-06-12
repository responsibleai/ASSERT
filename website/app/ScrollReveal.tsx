"use client";

import { useEffect } from "react";

export default function ScrollReveal() {
	useEffect(() => {
		const targets = document.querySelectorAll<HTMLElement>("[data-reveal]");
		if (!targets.length) return;

		if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
			targets.forEach((el) => el.classList.add("is-visible"));
			return;
		}

		const io = new IntersectionObserver(
			(entries) => {
				entries.forEach((entry) => {
					if (entry.isIntersecting) {
						(entry.target as HTMLElement).classList.add("is-visible");
						io.unobserve(entry.target);
					}
				});
			},
			{ threshold: 0.08, rootMargin: "0px 0px -10% 0px" }
		);

		targets.forEach((el) => io.observe(el));
		return () => io.disconnect();
	}, []);

	return null;
}
