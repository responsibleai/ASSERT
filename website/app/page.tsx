"use client";
/* eslint-disable @typescript-eslint/no-unused-vars */

import Image from "next/image";
import { useEffect, useState } from "react";
import HeroGrid from "./HeroGrid";
import MagicCard from "./MagicCard";
import ScrollReveal from "./ScrollReveal";
import TopNav from "./TopNav";
import OnThisPageNav from "./OnThisPageNav";
import CodeSnippets from "./CodeSnippets";
import { Terminal, TypingAnimation, AnimatedSpan } from "./Terminal";
import SystematizerDiagram from "./Systematizer";
import SystematizationExample from "./SystematizationExample";
import Plasma from "./Plasma";
import { Lens } from "./Lens";
import HowItWorks from "./HowItWorks";
import PartnerQuotes from "./PartnerQuotes";

const BASE_PATH = "/ASSERT";

function GitHubMark() {
	return (
		<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
			<path
				d="M8 0.58C3.9 0.58 0.58 3.9 0.58 8C0.58 11.27 2.69 14.05 5.62 15.03C5.99 15.1 6.12 14.87 6.12 14.68C6.12 14.52 6.11 14.02 6.11 13.48C4.09 13.85 3.57 12.99 3.41 12.53C3.32 12.3 2.95 11.59 2.64 11.41C2.39 11.27 2.03 10.93 2.63 10.92C3.19 10.91 3.59 11.43 3.72 11.66C4.36 12.74 5.37 12.43 6.14 12.24C6.2 11.78 6.39 11.47 6.6 11.29C4.81 11.09 2.94 10.4 2.94 7.35C2.94 6.48 3.24 5.78 3.76 5.24C3.69 5.06 3.42 4.31 3.84 3.3C3.84 3.3 4.46 3.11 6.11 4.22C6.71 4.05 7.35 3.97 8 3.97C8.65 3.97 9.29 4.05 9.89 4.22C11.54 3.1 12.16 3.3 12.16 3.3C12.58 4.31 12.31 5.06 12.24 5.24C12.76 5.78 13.06 6.47 13.06 7.35C13.06 10.41 11.18 11.09 9.39 11.29C9.66 11.52 9.9 11.95 9.9 12.62C9.9 13.58 9.89 14.36 9.89 14.68C9.89 14.87 10.02 15.1 10.39 15.03C13.31 14.05 15.42 11.26 15.42 8C15.42 3.9 12.1 0.58 8 0.58Z"
				fill="#F0F6FC"
			/>
		</svg>
	);
}

function ArrowUpRight() {
	return (
		<svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
			<path d="M7 17L17 7M17 7H8M17 7V16" stroke="#8b949e" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
		</svg>
	);
}

function ProductButtons({ githubUrl, docsUrl = "#", startUrl = "#" }: { githubUrl: string; docsUrl?: string; startUrl?: string }) {
	return (
		<div className="button-row">
			<a href={startUrl} className="btn btn-primary">Get started</a>
			<a href={docsUrl} className="btn btn-secondary" target="_blank" rel="noopener noreferrer">Documentation</a>
			<a href={githubUrl} target="_blank" rel="noopener noreferrer" className="btn btn-link">
				<GitHubMark />
				<span>GitHub</span>
			</a>
		</div>
	);
}

const policySteps = [
	{ title: "Behavior Spec", body: "Writing behavior description", icon: `${BASE_PATH}/icons/behavior_spec.svg` },
	{ title: "Behavior Policy", body: "Auto mapped what is allowed and not allowed.", icon: `${BASE_PATH}/icons/behavior_policy.svg` },
	{ title: "Evaluation Set", body: "Auto generated test input", icon: `${BASE_PATH}/icons/evaluation_set.svg` },
	{ title: "Your AI System", body: "Any model or agent, hosted, tool using or custom framework", icon: `${BASE_PATH}/icons/ai_system.svg` },
	{ title: "Judge", body: "Scores each response against the policy.", icon: `${BASE_PATH}/icons/judge.svg` }
];

const shieldStages = [
	{ stage: "Stage 1", title: "Input Validation", body: "Blocks jailbreaks, PII, and malicious prompts via regex + LLM classifier", chips: ["Regex", "Classifiers", "LLM"] },
	{ stage: "Stage 2", title: "State Validation", body: "Blocks jailbreaks, PII, and malicious prompts via regex + LLM classifier", chips: ["Deterministic"] },
	{ stage: "Stage 3", title: "Tool Execution Validation", body: "A second LLM reviews proposed tool calls against the full contract", chips: ["Regex", "Classifiers", "LLM"] },
	{ stage: "Stage 4", title: "Post-Tool Validation", body: "Inspects tool results for sensitive data before the agent processes them", chips: ["Regex", "Classifiers", "LLM"] },
	{ stage: "Stage 5", title: "Output Validation", body: "Scans final responses for PII leakage or sensitive data before delivery", chips: ["Regex", "Classifiers", "LLM"] }
];

const frameworkLogos: { src: string; alt: string }[] = [
	{ src: "langgraph logo.png", alt: "LangGraph" },
	{ src: "crewailogo.png", alt: "CrewAI" },
	{ src: "OpenAIlogo.png", alt: "OpenAI Agents SDK" },
	{ src: "DSPy logo.png", alt: "DSPy" },
	{ src: "llamalndexlogo.png", alt: "LlamaIndex" },
	{ src: "autogen logo.png", alt: "AutoGen / MAF" },
	{ src: "anthropic-logo.svg", alt: "Anthropic" },
	{ src: "nvidia-logo.webp", alt: "NVIDIA" },
	{ src: "vertex-ai-logo.png", alt: "Vertex AI" },
	// Add more logos here — drop the file into public/icons/ and add an entry.
];

const policyResources: { title: string; body?: string; href: string }[] = [
	{ title: "GitHub repo", body: "Browse the code repository.", href: "https://github.com/microsoft/ASSERT/" },
	{ title: "Get started", body: "Install the SDK and run your first evaluation in under 5 minutes.", href: "https://aka.ms/assert-get-started" },
	{ title: "Read the technical blog", body: "Learn more about how ASSERT works.", href: "https://commandline.microsoft.com/assert-written-intent-executable-evals/" },
	{ title: "Examples", body: "Take a look at sample config files and datasets created by ASSERT.", href: "https://aka.ms/assert-examples" }
];

const shieldResources = [
	{
		title: "Quick Start",
		body: "Install SDK and write your first guardrails contract in 5 minutes.",
		href: `${BASE_PATH}/agent-shield/docs/getting-started/overview/`,
	},
	{
		title: "Framework Support",
		body: "Adapters for LangChain, AutoGen, Semantic Kernel, OpenAI Agents, CrewAI, Anthropic, Node.js, and .NET.",
		href: `${BASE_PATH}/agent-shield/docs/getting-started/setup/`,
	},
	{
		title: "Examples",
		body: "Bank Managers, Sensitive Documents DLP and Endpoint Governance demos.",
		href: `${BASE_PATH}/agent-shield/docs/using/specification/overview/`,
	},
	{
		title: "Specification",
		body: "Formal spec for the guardrails YAML contract, composability model, and approval flows.",
		href: `${BASE_PATH}/agent-shield/docs/using/specification/overview/`,
	},
];

function ShineCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
	return <div className={`shine-card ${className}`}>{children}</div>;
}

export default function Home() {
	const FULL_TITLE = "ASSERT";
	const [typedTitle, setTypedTitle] = useState(FULL_TITLE);
	const [typingDone, setTypingDone] = useState(true);

	useEffect(() => {
		if (typeof window === "undefined") return;
		if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
			setTypedTitle(FULL_TITLE);
			setTypingDone(true);
			return;
		}
		setTypedTitle("");
		setTypingDone(false);
		let i = 0;
		const id = window.setInterval(() => {
			i += 1;
			setTypedTitle(FULL_TITLE.slice(0, i));
			if (i >= FULL_TITLE.length) {
				window.clearInterval(id);
				setTypingDone(true);
			}
		}, 120);
		return () => window.clearInterval(id);
	}, []);

	const policyHowItWorks = (
		<div className="policy-pipeline">
			<MagicCard className="policy-card-narrow">
				<div className="policy-card-inner">
					<div className="policy-card-eyebrow">Humans</div>
					<div className="policy-card-title">Write the Policy</div>
					<p>Developers can name the behavior that matters for this app.</p>
					<p>Policy experts can define what is allowed and what isn’t.</p>
				</div>
			</MagicCard>

			<MagicCard className="policy-card-wide">
				<div className="policy-card-inner">
					<div className="policy-card-eyebrow">Policy to metrics</div>
					<div className="policy-card-title">Adapts to the system, probes behavior, and judges responses.</div>
					<div className="policy-steps">
						{policySteps.map((step) => (
							<div key={step.title} className="policy-step">
								<div className="policy-step-icon" aria-hidden="true">
									<Image src={step.icon} alt="" width={24} height={24} />
								</div>
								<div className="policy-step-title">{step.title}</div>
								<div className="policy-step-body">{step.body}</div>
							</div>
						))}
					</div>
				</div>
			</MagicCard>
		</div>
	);

	const policyResourcesPanel = (
		<div className="resource-grid">
			{policyResources.map((resource) => (
				<ShineCard key={resource.title} className="resource-card">
					<a className="resource-inner" href={resource.href} target="_blank" rel="noopener noreferrer">
						<div className="resource-head">
							<div className="resource-title">{resource.title}</div>
							<ArrowUpRight />
						</div>
						{resource.body && <p>{resource.body}</p>}
					</a>
				</ShineCard>
			))}
		</div>
	);

	const shieldHowItWorks = (
		<div className="stage-grid">
			{shieldStages.map((stage) => (
				<MagicCard key={stage.title} className="stage-card">
					<div className="stage-inner">
						<div className="stage-eyebrow">{stage.stage}</div>
						<div className="stage-title">{stage.title}</div>
						<p>{stage.body}</p>
						<div className="stage-chips">
							{stage.chips.map((chip) => (
								<span key={chip} className="stage-chip">{chip}</span>
							))}
						</div>
					</div>
				</MagicCard>
			))}
		</div>
	);

	const shieldResourcesPanel = (
		<div className="resource-grid">
			{shieldResources.map((resource) => (
				<ShineCard key={resource.title} className="resource-card">
					<a className="resource-inner" href={resource.href} target="_blank" rel="noopener noreferrer">
						<div className="resource-head">
							<div className="resource-title">{resource.title}</div>
							<ArrowUpRight />
						</div>
						<p>{resource.body}</p>
					</a>
				</ShineCard>
			))}
		</div>
	);

	return (
		<main className="page-shell">
			<ScrollReveal />
			<TopNav />
			<OnThisPageNav />

			<section className="hero hero-assert" id="overview">
				<HeroGrid />
				<div className="hero-content hero-assert-content">
					<div className="hero-assert-copy">
						<h1 className="hero-assert-title">
							<span className="hero-assert-word">{typedTitle}</span>
							{!typingDone && <span className="hero-assert-caret" aria-hidden="true">&nbsp;</span>}
							<Image
								src={`${BASE_PATH}/icons/Logo.svg`}
								alt=""
								width={16}
								height={16}
								className="hero-assert-mark"
							/>
						</h1>
						<p className="hero-assert-subtitle">
							Adaptive Spec-driven Scoring for<br />
							Evaluation and Regression Testing
						</p>
						<p className="hero-assert-desc">
							Describe the behavior you care about. ASSERT generates a behavior taxonomy, stratified test scenarios, runs them against your target system, and produces policy-grounded verdicts with evidence.
						</p>
						<div className="hero-assert-actions">
							<a
								href="https://github.com/microsoft/ASSERT/"
								target="_blank"
								rel="noopener noreferrer"
								className="hero-link"
							>
								<span>GitHub</span>
								<svg width="18" height="18" viewBox="0 0 16 16" fill="none" aria-hidden="true">
									<path
										d="M8 0.58C3.9 0.58 0.58 3.9 0.58 8C0.58 11.27 2.69 14.05 5.62 15.03C5.99 15.1 6.12 14.87 6.12 14.68C6.12 14.52 6.11 14.02 6.11 13.48C4.09 13.85 3.57 12.99 3.41 12.53C3.32 12.3 2.95 11.59 2.64 11.41C2.39 11.27 2.03 10.93 2.63 10.92C3.19 10.91 3.59 11.43 3.72 11.66C4.36 12.74 5.37 12.43 6.14 12.24C6.2 11.78 6.39 11.47 6.6 11.29C4.81 11.09 2.94 10.4 2.94 7.35C2.94 6.48 3.24 5.78 3.76 5.24C3.69 5.06 3.42 4.31 3.84 3.3C3.84 3.3 4.46 3.11 6.11 4.22C6.71 4.05 7.35 3.97 8 3.97C8.65 3.97 9.29 4.05 9.89 4.22C11.54 3.1 12.16 3.3 12.16 3.3C12.58 4.31 12.31 5.06 12.24 5.24C12.76 5.78 13.06 6.47 13.06 7.35C13.06 10.41 11.18 11.09 9.39 11.29C9.66 11.52 9.9 11.95 9.9 12.62C9.9 13.58 9.89 14.36 9.89 14.68C9.89 14.87 10.02 15.1 10.39 15.03C13.31 14.05 15.42 11.26 15.42 8C15.42 3.9 12.1 0.58 8 0.58Z"
										fill="#F0F6FC"
									/>
								</svg>
							</a>
							<a href="https://commandline.microsoft.com/assert-written-intent-executable-evals/" target="_blank" rel="noopener noreferrer" className="hero-btn hero-btn-secondary">Read the blog</a>
							<a href="https://aka.ms/assert-get-started" target="_blank" rel="noopener noreferrer" className="hero-btn hero-btn-shine">
								<span className="hero-btn-shine-border" aria-hidden="true" />
								<span className="hero-btn-shine-label">Get started</span>
							</a>
						</div>
					</div>
					<div className="hero-assert-mock">
						<Plasma
							color="#643FB2"
							scale={1.1}
							direction="reverse"
							mouseInteractive={false}
							className="hero-assert-mock-plasma"
						/>
						<Lens
							className="hero-assert-mock-lens"
							style={{ position: "absolute" }}
							zoomFactor={1.6}
							lensSize={200}
							ariaLabel="Zoom into Assert UI snapshot"
						>
							<Image
								src={`${BASE_PATH}/icons/UI%20snapshot.png`}
								alt="Assert UI snapshot"
								width={1280}
								height={760}
								className="hero-assert-mock-img"
								priority
							/>
						</Lens>
						<Image
							src={`${BASE_PATH}/icons/tooltip.png`}
							alt=""
							width={420}
							height={140}
							className="hero-assert-mock-tooltip"
							priority
						/>
					</div>
				</div>
			</section>

			<section className="hero-why" id="why-assert">
				<div className="hero-why-inner">
					<div className="hero-why-content" data-reveal>
						<h2 className="hero-why-title">
							<span className="hero-why-focus">Why ASSERT</span>
						</h2>
						<div className="hero-why-body">
							<p>
								Most AI systems start with a specification: product requirements, policies, system prompts, or launch criteria describing what the system should and should not do.
							</p>
							<p>
								But evaluation often starts elsewhere: generic scorers, predefined benchmarks, or manual test cases that drift from the original intent.
							</p>
							<p>
								<strong className="hero-why-emph">ASSERT closes that gap.</strong> It turns your specified behaviors in natural language into structured, executable evaluations that can be reviewed, run, scored, and improved over time.
							</p>
						</div>
					</div>
				</div>
			</section>

			<div className="content-shell">
				{/* Policy to metrics — How it works + Resources stacked */}
				<section className="product-section" id="policy-metrics">
					<div className="product-subsection product-subsection-top" id="evaluation-spec" data-reveal>
						<div className="product-subsection-header">
							<div className="product-subsection-title-row">
								<h3 className="subsection-heading subsection-heading-lg">Start from an evaluation specification</h3>
							</div>
							<div className="subsection-body">
								<p>
									An ASSERT evaluation starts with the behavior you want to test and the system you want to test it against. The YAML config connects your natural-language behavior specification, target system, test generation settings, trace collection, and judge dimensions. From that config, ASSERT generates a behavior taxonomy, creates stratified test scenarios, runs them against your system, and scores the results.
								</p>
							</div>
						</div>
						<MagicCard className="code-snippets-card" disableHover>
							<div className="code-snippets-card-inner">
								<CodeSnippets />
							</div>
						</MagicCard>
					</div>

					<div className="product-subsection" id="run-evaluation" data-reveal>
						<div className="run-eval-grid">
							<div className="run-eval-copy">
								<HeroGrid />
								<p className="run-eval-headline">
									Once you&rsquo;ve configured your evaluation config file, run your evaluation with a single line of code
								</p>
							</div>
							<div className="run-eval-terminal">
								<Terminal title="Terminal">
									<TypingAnimation>assert-ai run --config eval_config.yaml</TypingAnimation>
									<AnimatedSpan className="terminal-line--ok">
										<span className="terminal-check">✓</span> Generated 12 behavior categories
									</AnimatedSpan>
									<AnimatedSpan className="terminal-line--ok">
										<span className="terminal-check">✓</span> Created 480 test scenarios
									</AnimatedSpan>
									<AnimatedSpan className="terminal-line--ok">
										<span className="terminal-check">✓</span> Ran 480 scenarios against travel-planner-v1
									</AnimatedSpan>
									<AnimatedSpan className="terminal-line--ok">
										<span className="terminal-check">✓</span> Scored policy_violation and overrefusal
									</AnimatedSpan>
									<AnimatedSpan className="terminal-line--ok">
										<span className="terminal-check">✓</span> Results ready in viewer
									</AnimatedSpan>
								</Terminal>
							</div>
						</div>
					</div>

					<div className="product-subsection" id="framework-agnostic" data-reveal>
						<div className="framework-grid">
							<div className="framework-copy">
								<h3 className="framework-heading">
									<span style={{ whiteSpace: "nowrap" }}>Run ASSERT</span>
									<br />
									<span style={{ whiteSpace: "nowrap" }}>
										against any target
									</span>
									<br />
									<span style={{ whiteSpace: "nowrap" }}>
										you can call from Python
									</span>
								</h3>
							</div>
							<div className="framework-logos-wrap">
								<p className="framework-desc">
									ASSERT is framework agnostic. The target can be a model, a RAG application, a prompt chain, a multi-agent workflow, or an opaque-box API. If you can invoke it from Python, ASSERT can generate evaluation test prompts and scenarios, inference them against your target, and score the results.
								</p>
							</div>
						</div>
						<div className="framework-stat-row" data-reveal>
							<h3 className="framework-heading framework-heading-stat">
								<span className="framework-stat-num">33+</span>{" "}
								<span className="framework-stat-label">
									Frameworks supported via{" "}
									<a
										href="https://github.com/Arize-ai/openinference"
										target="_blank"
										rel="noopener noreferrer"
										className="framework-stat-link"
									>
										OpenInference
									</a>
								</span>
							</h3>
							<h3 className="framework-heading framework-heading-stat">
								<span className="framework-stat-num">100+</span>{" "}
								<span className="framework-stat-label">
									LLM APIs via{" "}
									<a
										href="https://github.com/BerriAI/litellm"
										target="_blank"
										rel="noopener noreferrer"
										className="framework-stat-link"
									>
										LiteLLM
									</a>
								</span>
							</h3>
						</div>
						<div className="framework-marquee" data-reveal>
							<div className="framework-marquee-track">
								{[0, 1].map((dup) => (
									<div className="framework-marquee-group" key={dup} aria-hidden={dup === 1}>
										{frameworkLogos.map((logo) => (
											<div className="framework-logo" key={`${dup}-${logo.alt}`}>
												<img
													src={`${BASE_PATH}/icons/${logo.src}`}
													alt={logo.alt}
													className="framework-logo-img"
												/>
											</div>
										))}
									</div>
								))}
							</div>
						</div>
					</div>

					<div className="product-subsection" id="how-it-works" data-reveal>
						<HowItWorks />
					</div>

					<div className="product-subsection" id="systematization">
						<div className="product-subsection-header" data-reveal>
							<div className="product-subsection-title-row">
								<h3 className="subsection-heading subsection-heading-lg">Systematization &amp; Taxonomization</h3>
							</div>
						</div>
						<div className="systematization-grid" data-reveal>
							<div className="systematization-title">
								<HeroGrid />
								<h4 className="systematization-heading">
									Turning intent into
									<br />
									testable behavior
								</h4>
							</div>
							<div className="systematization-body">
								<p>
									Systematization &amp; Taxonomization is the step that turns a description of a concept, e.g., an open-ended behavior description, into a structured executable evaluation.
								</p>
								<p>
									Given a natural-language policy, ASSERT identifies behavior categories, defines policies such as permissible and impermissible for each category, and generates test cases reflecting coverage over those behaviors. This creates the bridge between human-written intent and executable test generation.
								</p>
							</div>
						</div>
						<div data-reveal>
							<MagicCard className="code-snippets-card" disableHover>
								<div className="code-snippets-card-inner">
									<SystematizationExample />
								</div>
							</MagicCard>
						</div>

						<div className="systematization-grid systematization-grid--how" data-reveal>
							<div className="systematization-title">
								<HeroGrid />
								<p className="systematization-lede">
									The systematizer produces this in three steps that mirror the approach of Agarwal et al. (2026)
								</p>
								<a
									href="https://arxiv.org/abs/2605.26001"
									target="_blank"
									rel="noreferrer"
									className="nav-btn nav-btn-secondary product-learn-more"
								>
									Read the paper
									<span className="learn-more-chevron" aria-hidden="true">›</span>
								</a>
							</div>
							<div className="systematization-body">
								<p>
									The systematizer transforms a broad concept (could be e.g., a system behavior, capability, etc.) into a concept spec, i.e., a structured, explicit representation centered on a set of patterns. Each pattern consists of a template with slots, slot values, key terms and definitions, and citations to the theories that justify it.
								</p>
							</div>
						</div>

						<div className="syst-diagram-wrap" aria-label="Systematization pipeline diagram" data-reveal>
							<div className="syst-diagram" data-reveal>
								<div className="syst-io syst-io--input">
									<span className="syst-io-label">User input</span>
									<span className="syst-io-chip">Concept Name and Description</span>
								</div>
								<div className="syst-io-line syst-io-line--in" aria-hidden="true" />

								<ol className="syst-steps">
									<li className="syst-step">
										<div className="syst-step-head">
											<span className="syst-step-num">01</span>
											<h5 className="syst-step-title">Contextualization</h5>
										</div>
										<p className="syst-step-desc">
											Conduct a literature survey to ground the systematization in existing theories.
										</p>
										<div className="syst-step-visual" aria-hidden="true">
											<svg viewBox="0 0 120 80" role="presentation">
												<defs>
													<radialGradient id="syst-lens-glass" cx="35%" cy="30%" r="75%">
														<stop offset="0%" stopColor="#ffffff" stopOpacity="0.45" />
														<stop offset="55%" stopColor="#AF86F5" stopOpacity="0.18" />
														<stop offset="100%" stopColor="#AF86F5" stopOpacity="0.06" />
													</radialGradient>
												</defs>
												<rect x="10" y="4" width="44" height="58" rx="4" className="syst-fill-soft" />
												<rect x="66" y="4" width="44" height="58" rx="4" className="syst-fill-soft" />
												<line x1="18" y1="16" x2="46" y2="16" className="syst-stroke-line" />
												<line x1="18" y1="24" x2="46" y2="24" className="syst-stroke-line" />
												<line x1="18" y1="32" x2="40" y2="32" className="syst-stroke-line" />
												<line x1="18" y1="40" x2="44" y2="40" className="syst-stroke-line" />
												<line x1="74" y1="16" x2="102" y2="16" className="syst-stroke-line" />
												<line x1="74" y1="24" x2="102" y2="24" className="syst-stroke-line" />
												<line x1="74" y1="32" x2="96" y2="32" className="syst-stroke-line" />
												<line x1="74" y1="40" x2="100" y2="40" className="syst-stroke-line" />
												{/* magnifying glass */}
												<circle cx="92" cy="58" r="12" fill="url(#syst-lens-glass)" stroke="#AF86F5" strokeWidth="2" />
												<circle cx="88" cy="54" r="2.5" fill="#ffffff" fillOpacity="0.55" />
												<line x1="101" y1="67" x2="112" y2="78" stroke="#AF86F5" strokeWidth="2.5" strokeLinecap="round" />
											</svg>
											<span className="syst-step-caption">Synthesized literature</span>
										</div>
									</li>

									<li className="syst-step-arrow" aria-hidden="true">
										<svg viewBox="0 0 24 24" role="presentation">
											<path d="M4 12h14M13 6l6 6-6 6" />
										</svg>
									</li>

									<li className="syst-step">
										<div className="syst-step-head">
											<span className="syst-step-num">02</span>
											<h5 className="syst-step-title">Simulated Perspectives</h5>
										</div>
										<p className="syst-step-desc">
											Use the literature review as context to generate and synthesize input from varying perspectives.
										</p>
										<div className="syst-step-visual" aria-hidden="true">
											<svg viewBox="0 0 120 80" role="presentation">
												<defs>
													<symbol id="syst-user-icon" viewBox="0 0 16 16">
														<path fill="var(--fgColor-muted)" d="M10.561 8.073a6.005 6.005 0 0 1 3.432 5.142.75.75 0 1 1-1.498.07 4.5 4.5 0 0 0-8.99 0 .75.75 0 0 1-1.498-.07 6.004 6.004 0 0 1 3.431-5.142 3.999 3.999 0 1 1 5.123 0ZM10.5 5a2.5 2.5 0 1 0-5 0 2.5 2.5 0 0 0 5 0Z" />
													</symbol>
													<linearGradient id="syst-conn-grad" x1="26" y1="0" x2="66" y2="0" gradientUnits="userSpaceOnUse">
														<stop offset="0%" stopColor="#AF86F5" stopOpacity="0.25" />
														<stop offset="100%" stopColor="#AF86F5" stopOpacity="1" />
													</linearGradient>
												</defs>
												{/* connector lines (rounded brackets w/ gradient) */}
												<g stroke="url(#syst-conn-grad)" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round">
													<path d="M26 12 H38 Q42 12 42 16 V36 Q42 40 46 40 H66" />
													<path d="M26 68 H38 Q42 68 42 64 V44 Q42 40 46 40 H66" />
													<path d="M26 40 H66" />
												</g>
												{/* person circles */}
												<circle cx="16" cy="12" r="10" fill="var(--bgColor-muted)" stroke="var(--borderColor-default)" strokeWidth="1" />
												<circle cx="16" cy="40" r="10" fill="var(--bgColor-muted)" stroke="var(--borderColor-default)" strokeWidth="1" />
												<circle cx="16" cy="68" r="10" fill="var(--bgColor-muted)" stroke="var(--borderColor-default)" strokeWidth="1" />
												<use href="#syst-user-icon" x="10.5" y="6.5" width="11" height="11" />
												<use href="#syst-user-icon" x="10.5" y="34.5" width="11" height="11" />
												<use href="#syst-user-icon" x="10.5" y="62.5" width="11" height="11" />
												{/* paper */}
												<rect x="66" y="11" width="44" height="58" rx="4" className="syst-fill-soft" />
												<line x1="74" y1="22" x2="102" y2="22" className="syst-stroke-line" />
												<line x1="74" y1="34" x2="102" y2="34" className="syst-stroke-line" />
												<line x1="74" y1="46" x2="100" y2="46" className="syst-stroke-line" />
												<line x1="74" y1="58" x2="96" y2="58" className="syst-stroke-line" />
											</svg>
											<span className="syst-step-caption syst-step-caption--right">
												Synthesized<br />output
											</span>
										</div>
									</li>

									<li className="syst-step-arrow" aria-hidden="true">
										<svg viewBox="0 0 24 24" role="presentation">
											<path d="M4 12h14M13 6l6 6-6 6" />
										</svg>
									</li>

									<li className="syst-step">
										<div className="syst-step-head">
											<span className="syst-step-num">03</span>
											<h5 className="syst-step-title">Concept Specification</h5>
										</div>
										<p className="syst-step-desc">
											Synthesize the concept spec and validate against systematization criteria.
										</p>
										<div className="syst-step-visual" aria-hidden="true">
											<svg viewBox="0 0 120 80" role="presentation">
												<defs>
													<linearGradient id="syst-step3-conn" x1="60" y1="20" x2="60" y2="32" gradientUnits="userSpaceOnUse">
														<stop offset="0%" stopColor="#AF86F5" stopOpacity="0.25" />
														<stop offset="100%" stopColor="#AF86F5" stopOpacity="1" />
													</linearGradient>
													<linearGradient id="syst-step3-bar" x1="0" y1="0" x2="1" y2="0">
												<stop offset="0%" stopColor="#3D1E78" />
												<stop offset="100%" stopColor="#643FB2" />
													</linearGradient>
												</defs>
												{/* Top: Content spec card */}
												<rect x="24" y="0" width="72" height="20" rx="3" className="syst-fill-soft" />
												<text x="60" y="13" textAnchor="middle" fontSize="8" fill="var(--fgColor-muted)" style={{ fontFamily: "var(--font-display)" }}>Content spec</text>
												{/* Gradient connector (same style as Simulated Perspectives) */}
												<path d="M60 20 V32" stroke="url(#syst-step3-conn)" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
												{/* Bottom: Validate against criteria card */}
												<rect x="4" y="32" width="112" height="48" rx="4" className="syst-fill-soft" />
												{/* Row 1: Clarity label */}
												<text x="10" y="43" fontSize="6" fill="var(--fgColor-muted)" style={{ fontFamily: "var(--font-display)" }}>Clarity</text>
												{/* Row 2: Clarity bar */}
												<rect x="10" y="48" width="100" height="4" rx="2" fill="var(--bgColor-default)" />
												<rect x="10" y="48" width="80" height="4" rx="2" fill="url(#syst-step3-bar)" />
												{/* Row 3: Granularity label */}
												<text x="10" y="64" fontSize="6" fill="var(--fgColor-muted)" style={{ fontFamily: "var(--font-display)" }}>Granularity</text>
												{/* Row 4: Granularity bar */}
												<rect x="10" y="69" width="100" height="4" rx="2" fill="var(--bgColor-default)" />
												<rect x="10" y="69" width="62" height="4" rx="2" fill="url(#syst-step3-bar)" />
											</svg>
										</div>
									</li>

									<li className="syst-step-arrow" aria-hidden="true">
										<svg viewBox="0 0 24 24" role="presentation">
											<path d="M4 12h14M13 6l6 6-6 6" />
										</svg>
									</li>

									<li className="syst-step">
										<div className="syst-step-head">
											<span className="syst-step-num">04</span>
											<h5 className="syst-step-title">Policy Specification</h5>
										</div>
										<p className="syst-step-desc">
											Convert the concept spec into a taxonomy of permissible and impermissible behaviors.
										</p>
										<div className="syst-step-visual" aria-hidden="true">
											<svg viewBox="0 0 120 64" role="presentation">
												<defs>
													<linearGradient id="syst-step4-conn" x1="40" y1="32" x2="46" y2="32" gradientUnits="userSpaceOnUse">
														<stop offset="0%" stopColor="#AF86F5" stopOpacity="0.25" />
														<stop offset="100%" stopColor="#AF86F5" stopOpacity="1" />
													</linearGradient>
												</defs>
												{/* Paper (wider, 4 lines) */}
												<rect x="4" y="4" width="36" height="56" rx="3" className="syst-fill-soft" />
												<line x1="10" y1="14" x2="36" y2="14" className="syst-stroke-line" />
												<line x1="10" y1="24" x2="36" y2="24" className="syst-stroke-line" />
												<line x1="10" y1="34" x2="36" y2="34" className="syst-stroke-line" />
												<line x1="10" y1="44" x2="30" y2="44" className="syst-stroke-line" />
												{/* Gradient connector (same style as other steps) */}
												<path d="M40 32 H46" stroke="url(#syst-step4-conn)" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
												{/* Policy box */}
												<rect x="46" y="12" width="70" height="40" rx="4" className="syst-fill-soft" />
												{/* Permissible row */}
												<circle cx="51" cy="24" r="2" fill="#2D8345" />
												<path d="M50 24 L50.8 24.8 L52 23.2" stroke="#fff" strokeWidth="0.7" fill="none" strokeLinecap="round" strokeLinejoin="round" />
												<text x="55" y="25.7" fill="#7CD992" fontSize="5" style={{ fontFamily: "var(--font-mono)" }}>Permissible</text>
												{/* Not permissible row */}
												<circle cx="51" cy="40" r="2" fill="#C84A4A" />
												<path d="M50 39 L52 41 M52 39 L50 41" stroke="#fff" strokeWidth="0.7" strokeLinecap="round" />
												<text x="55" y="41.7" fill="#E89A8A" fontSize="5" style={{ fontFamily: "var(--font-mono)" }}>Not Permissible</text>
											</svg>
											<span className="syst-step-caption">behavior taxonomy</span>
										</div>
									</li>
								</ol>

								<div className="syst-io-line syst-io-line--out" aria-hidden="true" />
								<div className="syst-io syst-io--output">
									<span className="syst-io-chip">Behavior Taxonomy</span>
									<span className="syst-io-label">System output</span>
								</div>
							</div>
						</div>

					</div>

					<div className="product-subsection" id="partners" data-reveal>
						<PartnerQuotes />
					</div>

					<div className="product-subsection" id="resources" data-reveal>
						<h3 className="subsection-heading subsection-heading-lg">Resources</h3>
						{policyResourcesPanel}
					</div>
				</section>
			</div>

			<footer className="page-footer">
				<p>Made responsibly with 💜 by Microsoft</p>
			</footer>
		</main>
	);
}
