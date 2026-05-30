"use client";

import { useEffect, useRef, useState } from "react";

type Props = {
	squareSize?: number;
	gridGap?: number;
	flickerChance?: number;
	color?: string;
	maxOpacity?: number;
	className?: string;
};

function toRGBA(color: string) {
	if (typeof window === "undefined") return "0, 0, 0";
	const canvas = document.createElement("canvas");
	canvas.width = canvas.height = 1;
	const ctx = canvas.getContext("2d");
	if (!ctx) return "255, 0, 0";
	ctx.fillStyle = color;
	ctx.fillRect(0, 0, 1, 1);
	const [r, g, b] = Array.from(ctx.getImageData(0, 0, 1, 1).data);
	return `${r}, ${g}, ${b}`;
}

export default function FlickeringGrid({
	squareSize = 4,
	gridGap = 6,
	flickerChance = 0.3,
	color = "#587760",
	maxOpacity = 0.3,
	className
}: Props) {
	const containerRef = useRef<HTMLDivElement | null>(null);
	const canvasRef = useRef<HTMLCanvasElement | null>(null);
	const [size, setSize] = useState({ width: 0, height: 0 });

	useEffect(() => {
		const el = containerRef.current;
		if (!el) return;
		const ro = new ResizeObserver((entries) => {
			const r = entries[0].contentRect;
			setSize({ width: r.width, height: r.height });
		});
		ro.observe(el);
		return () => ro.disconnect();
	}, []);

	useEffect(() => {
		const canvas = canvasRef.current;
		if (!canvas || size.width === 0 || size.height === 0) return;
		const ctx = canvas.getContext("2d");
		if (!ctx) return;

		const dpr = window.devicePixelRatio || 1;
		canvas.width = size.width * dpr;
		canvas.height = size.height * dpr;
		canvas.style.width = `${size.width}px`;
		canvas.style.height = `${size.height}px`;
		ctx.scale(dpr, dpr);

		const cell = squareSize + gridGap;
		const cols = Math.ceil(size.width / cell);
		const rows = Math.ceil(size.height / cell);
		const rgb = toRGBA(color);
		const opacities = new Float32Array(cols * rows).map(
			() => Math.random() * maxOpacity
		);

		let raf = 0;
		let last = performance.now();

		const tick = (now: number) => {
			const dt = (now - last) / 1000;
			last = now;
			for (let i = 0; i < opacities.length; i++) {
				if (Math.random() < flickerChance * dt) {
					opacities[i] = Math.random() * maxOpacity;
				}
			}
			ctx.clearRect(0, 0, size.width, size.height);
			for (let y = 0; y < rows; y++) {
				for (let x = 0; x < cols; x++) {
					const o = opacities[y * cols + x];
					ctx.fillStyle = `rgba(${rgb}, ${o})`;
					ctx.fillRect(x * cell, y * cell, squareSize, squareSize);
				}
			}
			raf = requestAnimationFrame(tick);
		};
		raf = requestAnimationFrame(tick);
		return () => cancelAnimationFrame(raf);
	}, [size, squareSize, gridGap, flickerChance, color, maxOpacity]);

	return (
		<div ref={containerRef} className={className} aria-hidden="true">
			<canvas ref={canvasRef} style={{ display: "block" }} />
		</div>
	);
}
