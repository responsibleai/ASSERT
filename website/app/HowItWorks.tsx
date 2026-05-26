"use client";

import { motion, useInView } from "framer-motion";
import { useRef, type ReactNode } from "react";
import BorderBeam from "./BorderBeam";

type HowCard = {
	id: string;
	title: string;
	description: string;
	extra?: ReactNode;
};

const ICONS: Record<string, ReactNode> = {
	specify: (
		<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
			<path d="M7 3h7l5 5v13H7z" />
			<path d="M14 3v5h5" />
			<path d="M9.5 12h7" />
			<path d="M9.5 15.5h7" />
			<path d="M9.5 18h4" />
			<path d="M16.5 18.5l1.5 1.5" />
		</svg>
	),
	systematize: (
		<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
			<circle cx="6" cy="6" r="2.2" />
			<circle cx="18" cy="6" r="2.2" />
			<circle cx="12" cy="13" r="2.2" />
			<circle cx="6" cy="19" r="2.2" />
			<circle cx="18" cy="19" r="2.2" />
			<path d="M7.5 7.3L10.6 11.8" />
			<path d="M16.5 7.3L13.4 11.8" />
			<path d="M10.6 14.2L7.5 17.8" />
			<path d="M13.4 14.2L16.5 17.8" />
		</svg>
	),
	"generate-test-set": (
		<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
			<rect x="7" y="3" width="12" height="15" rx="1.5" />
			<path d="M5 6v15h12" />
			<path d="M10 8h6" />
			<path d="M10 11h6" />
			<path d="M10 14h4" />
		</svg>
	),
	inference: (
		<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
			<circle cx="12" cy="12" r="9" />
			<circle cx="12" cy="12" r="5.5" />
			<circle cx="12" cy="12" r="2" fill="currentColor" stroke="none" />
		</svg>
	),
	judge: (
		<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
			<path d="M12 4v16" />
			<path d="M6 20h12" />
			<path d="M5 8h14" />
			<path d="M5 8l-2.5 5h5z" />
			<path d="M19 8l-2.5 5h5z" />
		</svg>
	),
	inspect: (
		<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
			<circle cx="10.5" cy="10.5" r="6.5" />
			<path d="M15.5 15.5L20 20" />
		</svg>
	),
};

const CARDS: HowCard[] = [
	{
		id: "specify",
		title: "Specify",
		description:
			"Input behavior description and system context in natural language.",
	},
	{
		id: "systematize",
		title: "Systematize & Taxonomize",
		description:
			"Transform broad concept (e.g., system behavior, capability, etc) from input into structured, explicit, and granular representation. Generate behavior taxonomy with auto-encoded policies of allowed or not allowed.",
		extra: (
			<>
				<span className="hiw-generates-label">Generates:</span>
				<ul className="hiw-generates-list">
					<li>Taxonomy with policies</li>
				</ul>
			</>
		),
	},
	{
		id: "generate-test-set",
		title: "Generate test set",
		description:
			"Create stratified test set of benign and adversarial test cases based on the taxonomy of behavior categories. Specify test set dimensions to stratify the test set against.",
		extra: (
			<>
				<span className="hiw-generates-label">Generates:</span>
				<ul className="hiw-generates-list">
					<li>
						<strong>Prompts</strong> — single-turn test cases.
					</li>
					<li>
						<strong>Scenarios</strong> — multi-turn tests based on a scenario
						that will be simulated by a tester model.
					</li>
				</ul>
			</>
		),
	},
	{
		id: "inference",
		title: "Inference against target",
		description:
			"Run the test set against any model, application, or agent and collect responses and traces.",
		extra: (
			<>
				<span className="hiw-generates-label">Generates:</span>
				<ul className="hiw-generates-list">
					<li>Inference set</li>
				</ul>
			</>
		),
	},
	{
		id: "judge",
		title: "Judge",
		description:
			"Score results against the policies in the taxonomy based on user-specified judge dimensions.",
		extra: (
			<>
				<span className="hiw-generates-label">Generates:</span>
				<ul className="hiw-generates-list">
					<li>Evaluation scores</li>
				</ul>
			</>
		),
	},
	{
		id: "inspect",
		title: "Inspect",
		description:
			"Review failures by behavior and scenario, or drill down into transcripts and traces.",
	},
];

function Card({ card, index }: { card: HowCard; index: number }) {
	const ref = useRef<HTMLElement | null>(null);
	// Active when the card intersects a thin band ~55%–75% of the viewport
	// (centered around ~65%). rootMargin format: top right bottom left.
	const isActive = useInView(ref, { margin: "-55% 0px -25% 0px" });

	return (
		<motion.article
			ref={ref}
			className={`hiw-card${isActive ? " is-beam-active" : ""}`}
			initial={{ opacity: 0, y: 48, filter: "blur(8px)" }}
			whileInView={{ opacity: 1, y: 0, filter: "blur(0px)" }}
			viewport={{ once: true, margin: "0px 0px -15% 0px" }}
			transition={{
				duration: 0.7,
				ease: [0.16, 1, 0.3, 1],
				delay: 0.05,
			}}
		>
			<div className="hiw-card-inner">
				<div className="hiw-card-index" aria-hidden="true">
					<span className="hiw-card-icon">{ICONS[card.id]}</span>
				</div>
				<div className="hiw-card-body">
					<h4 className="hiw-card-title">
						<span className="hiw-card-num">{String(index + 1).padStart(2, "0")}</span>
						<span className="hiw-card-title-text">{card.title}</span>
					</h4>
					<p className="hiw-card-desc">{card.description}</p>
					{card.extra ? <div className="hiw-card-extra">{card.extra}</div> : null}
				</div>
			</div>
			<BorderBeam
				size={260}
				duration={6}
				colorFrom="#AF86F5"
				colorTo="#643FB2"
			/>
		</motion.article>
	);
}

export default function HowItWorks() {
	return (
		<div className="hiw-grid">
			<aside className="hiw-left">
				<div className="hiw-left-sticky">
					<h3 className="hiw-eyebrow">How it works</h3>
					<p className="hiw-lede">ASSERT Evaluation framework</p>
					<p className="hiw-sublede">Specification-driven evaluation for models and agents.</p>
					<a href="#" className="nav-btn nav-btn-secondary product-learn-more hiw-learn-more">
						Learn more
						<span className="learn-more-chevron" aria-hidden="true">›</span>
					</a>
				</div>
			</aside>
			<div className="hiw-right">
				{CARDS.map((card, i) => (
					<Card key={card.id} card={card} index={i} />
				))}
			</div>
		</div>
	);
}
