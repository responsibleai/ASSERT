"use client";

import type { CSSProperties } from "react";

type Props = {
	size?: number;
	duration?: number;
	colorFrom?: string;
	colorTo?: string;
	borderWidth?: number;
};

export default function BorderBeam({
	size = 120,
	duration = 6,
	colorFrom = "#AF86F5",
	colorTo = "#643FB2",
	borderWidth = 1,
}: Props) {
	return (
		<div
			aria-hidden="true"
			className="border-beam"
			style={
				{
					"--border-beam-size": `${size}px`,
					"--border-beam-duration": `${duration}s`,
					"--border-beam-color-from": colorFrom,
					"--border-beam-color-to": colorTo,
					"--border-beam-width": `${borderWidth}px`,
				} as CSSProperties
			}
		>
			<span className="border-beam-spark" />
		</div>
	);
}
