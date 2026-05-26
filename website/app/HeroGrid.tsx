"use client";

import { useEffect, useRef } from "react";

export default function HeroGrid() {
	const ref = useRef<HTMLDivElement>(null);

	useEffect(() => {
		const el = ref.current;
		if (!el) return;
		const handle = (e: MouseEvent) => {
			const rect = el.getBoundingClientRect();
			el.style.setProperty("--mx", `${e.clientX - rect.left}px`);
			el.style.setProperty("--my", `${e.clientY - rect.top}px`);
		};
		const reset = () => {
			el.style.setProperty("--mx", `-9999px`);
			el.style.setProperty("--my", `-9999px`);
		};
		el.addEventListener("mousemove", handle);
		el.addEventListener("mouseleave", reset);
		return () => {
			el.removeEventListener("mousemove", handle);
			el.removeEventListener("mouseleave", reset);
		};
	}, []);

	return (
		<div ref={ref} className="hero-grid-wrap" aria-hidden="true">
			<div className="hero-grid" />
			<div className="hero-grid hero-grid--hot" />
		</div>
	);
}
