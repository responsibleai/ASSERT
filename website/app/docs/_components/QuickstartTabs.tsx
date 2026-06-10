"use client";

import { useState } from "react";

// Self-contained quickstart with three onboarding paths. Rendered by
// MarkdownContent in place of the `<!--quickstart-tabs-->` sentinel in
// docs/getting-started.md. The same three paths are authored as plain Markdown
// between the sentinels so GitHub (which can't run this component) still shows
// readable sections.

type OS = "bash" | "powershell";

const TABS = [
	{ id: "agent", label: "I have an agent" },
	{ id: "spec", label: "I have the agent spec" },
	{ id: "start", label: "Help me start" },
] as const;

function Code({ children }: { children: string }) {
	return (
		<pre className="quickstart-code">
			<code>{children}</code>
		</pre>
	);
}

function envLine(os: OS): string {
	return os === "powershell"
		? '# set your model key: $env:AZURE_API_KEY / $env:AZURE_API_BASE (or create a .env here \u2014 auto-loaded)'
		: "# set your model key: export AZURE_API_KEY / AZURE_API_BASE (or create a .env here \u2014 auto-loaded)";
}

export default function QuickstartTabs() {
	const [active, setActive] = useState<(typeof TABS)[number]["id"]>("agent");
	const [os, setOS] = useState<OS>("bash");

	return (
		<div className="quickstart">
			<div className="quickstart-tablist" role="tablist" aria-label="Quickstart paths">
				{TABS.map((t) => (
					<button
						key={t.id}
						role="tab"
						aria-selected={active === t.id}
						className={`quickstart-tab${active === t.id ? " is-active" : ""}`}
						onClick={() => setActive(t.id)}
					>
						{t.label}
					</button>
				))}
				<div className="quickstart-os" role="group" aria-label="Operating system">
					<button
						className={`quickstart-os-btn${os === "bash" ? " is-active" : ""}`}
						onClick={() => setOS("bash")}
					>
						macOS / Linux
					</button>
					<button
						className={`quickstart-os-btn${os === "powershell" ? " is-active" : ""}`}
						onClick={() => setOS("powershell")}
					>
						Windows
					</button>
				</div>
			</div>

			{active === "agent" && (
				<div role="tabpanel" className="quickstart-panel">
					<p>
						Connect an agent you already built (LangGraph powers ~half of agent
						builds; CrewAI, OpenAI Agents SDK, LlamaIndex, AutoGen, or a custom
						loop work the same way). Two lines of trace capture let the judge
						score tool calls and routing — not just the final text.
					</p>
					<Code>{`pip install "assert-ai[otel]"\n${envLine(os)}`}</Code>
					<p>Wrap your agent&apos;s entry function so its spans are captured:</p>
					<Code>{`# eval_target.py
from assert_ai import auto_trace
auto_trace.enable()                 # the 2 lines: judge sees tool calls + routing

from my_app import run_agent        # your existing agent entry function`}</Code>
					<p>Point the eval at it:</p>
					<Code>{`# eval_config.yaml
pipeline:
  inference:
    target:
      callable: eval_target:run_agent   # module:function
      trace:
        backend: phoenix`}</Code>
					<Code>{`assert-ai run --config eval_config.yaml`}</Code>
					<p className="quickstart-aside">
						Want to watch a worked example run end-to-end first?
					</p>
					<Code>{`pip install "assert-ai[langgraph,otel]"
assert-ai run --example travel-planner-langgraph`}</Code>
				</div>
			)}

			{active === "spec" && (
				<div role="tabpanel" className="quickstart-panel">
					<p>
						You have a system prompt or a written description of how the agent
						should behave, but no code to wire up yet. ASSERT evaluates the spec
						directly as a Prompt Agent — runs on a base install.
					</p>
					<Code>{`pip install assert-ai\n${envLine(os)}`}</Code>
					<p>Run the bundled health-assistant example:</p>
					<Code>{`assert-ai run --example health-assistant`}</Code>
					<p className="quickstart-aside">
						Swap in your own spec by editing the <code>target.system_prompt</code>{" "}
						and <code>behavior.description</code> in the generated config.
					</p>
				</div>
			)}

			{active === "start" && (
				<div role="tabpanel" className="quickstart-panel">
					<p>
						No spec yet? Describe your system in one line and an LLM assistant
						interviews you, then writes a complete <code>eval_config.yaml</code>.
					</p>
					<Code>{`pip install assert-ai\n${envLine(os)}`}</Code>
					<Code>{`assert-ai init --describe "a customer-support bot for an online bank"
assert-ai run --config eval_config.yaml`}</Code>
				</div>
			)}
		</div>
	);
}
