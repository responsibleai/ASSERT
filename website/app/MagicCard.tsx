"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

type Props = {
	children: ReactNode;
	className?: string;
	gradientSize?: number;
	gradientColor?: string;
	gradientOpacity?: number;
	gradientFrom?: string;
	gradientTo?: string;
	disableHover?: boolean;
};

export default function MagicCard({
	children,
	className = "",
	gradientSize = 220,
	gradientColor = "#4493F8",
	gradientOpacity = 0.45,
	gradientFrom = "#4493F8",
	gradientTo = "#1F6FEB",
	disableHover = false
}: Props) {
	const ref = useRef<HTMLDivElement | null>(null);
	const [pos, setPos] = useState({ x: -gradientSize, y: -gradientSize });
	const [active, setActive] = useState(false);

	useEffect(() => {
		const el = ref.current;
		if (!el) return;
		if (disableHover) return;
		const onMove = (e: MouseEvent) => {
			const r = el.getBoundingClientRect();
			setPos({ x: e.clientX - r.left, y: e.clientY - r.top });
		};
		const onEnter = () => setActive(true);
		const onLeave = () => setActive(false);
		el.addEventListener("mousemove", onMove);
		el.addEventListener("mouseenter", onEnter);
		el.addEventListener("mouseleave", onLeave);
		return () => {
			el.removeEventListener("mousemove", onMove);
			el.removeEventListener("mouseenter", onEnter);
			el.removeEventListener("mouseleave", onLeave);
		};
	}, [disableHover]);

	return (
		<div ref={ref} className={`magic-card ${className}`}>
			<div
				className="magic-card__border"
				style={{
					opacity: active ? 1 : 0,
					background: `radial-gradient(${gradientSize}px circle at ${pos.x}px ${pos.y}px, ${gradientFrom}, #1F6FEB 45%, ${gradientTo} 60%, #6e7681 75%, transparent 90%)`
				}}
				aria-hidden="true"
			/>
			<div
				className="magic-card__glow"
				style={{
					opacity: active ? gradientOpacity : 0,
					background: `radial-gradient(${gradientSize}px circle at ${pos.x}px ${pos.y}px, ${gradientColor}, transparent 70%)`
				}}
				aria-hidden="true"
			/>
			<div className="magic-card__content">{children}</div>
		</div>
	);
}
