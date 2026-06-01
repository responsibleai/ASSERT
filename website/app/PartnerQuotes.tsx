"use client";

import { useState } from "react";

const BASE_PATH = "/ASSERT";

type Quote = {
	brand: string;
	logo: string;
	emoji?: string;
	author: string;
	role: string;
	quote: string;
	accent?: string;
};

const QUOTES: Quote[] = [
	{
		brand: "Arize",
		logo: "arize-logo.png",
		author: "Aparna Dhinakaran",
		role: "Co-founder & Chief Product Officer, Arize AI",
		accent: "#7C5CFF",
		quote:
			"OpenInference exists so that developers can pick the agent framework they love and the observability they trust, without having to choose between them. ASSERT adopting OpenInference as its trace contract means a developer who instruments their LangGraph, CrewAI, LlamaIndex, or any of the dozens of supported frameworks today gets spec-driven evaluation with Arize observability with Phoenix and AX today — no rewriting of agent code, no lock-in to any one platform.",
	},
	{
		brand: "Pipecat",
		logo: "pipecat-logo.svg",
		author: "Kwindla Hultman Kramer",
		role: "CEO, Daily",
		accent: "#00D4A0",
		quote:
			"Voice agents are where evaluation gets hardest — real-time, multimodal, multi-turn — and most eval tools simply don't speak that language. With ASSERT, our developers pipe Pipecat traces in through OpenTelemetry and get scenario-specific behavior evaluation on the same voice flows they ship to production. That's the framework-agnostic ecosystem path voice AI developers need to succeed at scale and in demanding use cases.",
	},
	{
		brand: "LiteLLM",
		logo: "litellm-logo.png",
		author: "Krrish Dholakia",
		role: "CEO, LiteLLM",
		accent: "#34D399",
		quote:
			"LiteLLM gives developers one API for 100+ LLMs; ASSERT gives them one evaluation substrate for every agent. The two pair naturally — ASSERT runs on LiteLLM under the hood, so a developer can scenario-evaluate any of those 100+ models without rewiring anything. That's the multi-model, multi-provider future agent builders actually need.",
	},
	{
		brand: "Pydantic",
		logo: "pydantic-logo.svg",
		author: "Samuel Colvin",
		role: "CEO, Pydantic",
		accent: "#E91E63",
		quote:
			"PydanticAI gives developers a type-safe way to build agents in Python — type-safe evaluation is the natural next step. ASSERT picks up PydanticAI, runs through OpenInference with no SDK to add, turns a plain-English spec into rigorous scoring, and gives our community the same evaluation substrate that the larger frameworks get. That fits how Python developers actually want to work: validated inputs, validated outputs, and now validated behavior.",
	},
	{
		brand: "CrewAI",
		logo: "crewailogo.png",
		author: "Lorenze Jay",
		role: "Open Source Lead, CrewAI",
		accent: "#FF6B35",
		quote:
			"My favorite thing about ASSERT is that the eval is easy to configure and reason about. I describe the behavior I care about in YAML, point it at a real agent, and get artifacts back. Not just pass/fail. They show why the judge made each call. That openness matters. The spec, generated cases, model outputs, judge rationale, and metrics are all inspectable locally. The eval feels auditable, not like a black box.",
	},
];

function BrandLogo({ quote }: { quote: Quote }) {
	const [errored, setErrored] = useState(false);
	if (errored || !quote.logo) {
		return (
			<span
				className={`quote-card-logo-fallback quote-card-logo-fallback--${quote.brand.toLowerCase()}`}
				aria-hidden="true"
			>
				{quote.emoji ? <span className="quote-card-logo-fallback-emoji">{quote.emoji}</span> : null}
				{quote.brand}
			</span>
		);
	}
	// Use a plain <img> so wide wordmark logos can scale to native aspect ratio
	// (next/image requires fixed width/height which crops or distorts wordmarks).
	return (
		// eslint-disable-next-line @next/next/no-img-element
		<img
			src={`${BASE_PATH}/icons/${quote.logo}`}
			alt={`${quote.brand} logo`}
			className={`quote-card-logo quote-card-logo--${quote.brand.toLowerCase()}`}
			onError={() => setErrored(true)}
		/>
	);
}

function QuoteCard({ q }: { q: Quote }) {
	return (
		<figure className="quote-card">
			<header className="quote-card-header">
				<div className="quote-card-meta">
					<span className="quote-card-brand">{q.brand}</span>
					<span className="quote-card-tag">Assert framework partner</span>
				</div>
				<BrandLogo quote={q} />
			</header>
			<blockquote className="quote-card-body">{q.quote}</blockquote>
			<figcaption className="quote-card-author">
				<span className="quote-card-author-name">— {q.author}</span>
				<span className="quote-card-author-role">{q.role}</span>
			</figcaption>
		</figure>
	);
}

function MarqueeRow({ items, reverse = false }: { items: Quote[]; reverse?: boolean }) {
	// Duplicate the list so the translateX(-50%) loop appears seamless.
	const doubled = [...items, ...items];
	return (
		<div className="quotes-marquee" data-reverse={reverse ? "true" : "false"}>
			<div className="quotes-marquee-track" aria-hidden={false}>
				{doubled.map((q, i) => (
					<QuoteCard key={`${q.brand}-${i}`} q={q} />
				))}
			</div>
		</div>
	);
}

export default function PartnerQuotes() {
	// Split 5 quotes into two rows that scroll in opposite directions.
	const row1 = QUOTES.slice(0, 3);
	const row2 = [QUOTES[3], QUOTES[4], QUOTES[0]]; // pad row 2 to 3 items for balance

	return (
		<div className="quotes-section" data-reveal>
			<div className="quotes-header">
				<h3 className="subsection-heading subsection-heading-lg">
					Trusted by AI framework partners
				</h3>
				<p className="quotes-lede">
					Read what teams building agent infrastructure are saying about ASSERT.
				</p>
			</div>

			<div className="quotes-marquee-stack">
				<MarqueeRow items={row1} />
				<MarqueeRow items={row2} reverse />
			</div>
		</div>
	);
}
