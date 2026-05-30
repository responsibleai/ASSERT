"use client";

import {
	Children,
	cloneElement,
	isValidElement,
	useEffect,
	useRef,
	useState,
	type CSSProperties,
	type ReactElement,
	type ReactNode
} from "react";

type LineProps = {
	children: ReactNode;
	className?: string;
	delay?: number;
	duration?: number;
	__index?: number;
	__start?: boolean;
	__onDone?: (i: number) => void;
};

export function TypingAnimation({
	children,
	className,
	duration = 55,
	__index = 0,
	__start = false,
	__onDone
}: LineProps) {
	const text = typeof children === "string" ? children : "";
	const [shown, setShown] = useState("");
	const doneRef = useRef(false);

	useEffect(() => {
		if (doneRef.current) return;
		if (!__start) {
			setShown("");
			return;
		}
		let i = 0;
		const id = setInterval(() => {
			i++;
			setShown(text.slice(0, i));
		if (i >= text.length) {
				clearInterval(id);
				if (!doneRef.current) {
					doneRef.current = true;
					setTimeout(() => __onDone?.(__index), 500);
				}
			}
		}, duration);
		return () => clearInterval(id);
	}, [__start, text, duration, __index, __onDone]);

	return (
		<div className={`terminal-line terminal-line--type ${className ?? ""}`}>
			<span className="terminal-prompt">&gt;</span> {shown}
			<span className="terminal-caret" aria-hidden="true">▍</span>
		</div>
	);
}

export function AnimatedSpan({
	children,
	className,
	__index = 0,
	__start = false,
	__onDone
}: LineProps) {
	const [visible, setVisible] = useState(false);
	useEffect(() => {
		if (!__start) {
			setVisible(false);
			return;
		}
		setVisible(true);
		const id = setTimeout(() => __onDone?.(__index), 700);
		return () => clearTimeout(id);
	}, [__start, __index, __onDone]);

	const style: CSSProperties = {
		opacity: visible ? 1 : 0,
		transform: visible ? "translateY(0)" : "translateY(4px)",
		transition: "opacity 220ms ease, transform 220ms ease"
	};

	return (
		<div className={`terminal-line ${className ?? ""}`} style={style}>
			{children}
		</div>
	);
}

export function Terminal({
	children,
	className,
	title = "Agent Shield"
}: {
	children: ReactNode;
	className?: string;
	title?: string;
}) {
	const containerRef = useRef<HTMLDivElement | null>(null);
	const [active, setActive] = useState(false);
	const [step, setStep] = useState(0);

	useEffect(() => {
		const el = containerRef.current;
		if (!el) return;
		const io = new IntersectionObserver(
			(entries) => {
				if (entries[0].isIntersecting) setActive(true);
			},
			{ threshold: 0.3 }
		);
		io.observe(el);
		return () => io.disconnect();
	}, []);

	const onDone = (i: number) => {
		setStep((s) => Math.max(s, i + 1));
	};

	const lines = Children.toArray(children).filter(isValidElement) as ReactElement<LineProps>[];

	return (
		<div ref={containerRef} className={`terminal ${className ?? ""}`}>
			<div className="terminal-chrome">
				<span className="terminal-dot terminal-dot--red" />
				<span className="terminal-dot terminal-dot--yellow" />
				<span className="terminal-dot terminal-dot--green" />
				<span className="terminal-title">{title}</span>
			</div>
			<div className="terminal-body">
				{lines.map((child, i) =>
					cloneElement(child, {
						key: i,
						__index: i,
						__start: active && step >= i,
						__onDone: onDone
					})
				)}
			</div>
		</div>
	);
}
