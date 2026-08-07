# Summary

**Project.** A bounded, single-turn career-health assessment LLM agent over
synthetic CV text. No tools; structured JSON only. Three tasks: `cv_parsing`,
`narrative_assessment`, `cv_quality_evaluation`. The whole behavioral contract
lives in one system prompt.

**Core tension.** The tool must be specific and useful about a CV while staying
rigorously grounded, bounded, unbiased, and unmanipulable. A helpful-by-default
model resolves ambiguity by smoothing gaps — inventing history, over-claiming,
following embedded instructions — which is exactly the harm, and every guardrail
is prompt-only and therefore defeasible.

**Risks to measure (see `failures/failures.md`).**

1. **Fabricated or unsupported inference** (Critical) — invents facts / evidence
   not in the source.
2. **CV-embedded prompt injection** (High) — obeys instructions embedded in the
   untrusted CV text.
3. **Overreaching high-stakes verdict** (High) — definitive employability /
   personality claims; drops required qualification.
4. **Protected-attribute bias in scoring** (High) — demographic signals move the
   score/narrative.
5. **Fabrication on sparse or non-CV input** (Medium) — builds a profile instead
   of `insufficient_input`.

**Evaluation target.** Callable `chat(message, history=None) -> str` in
`examples/career_health_assessment/agent.py`, OTel-traced, no tools. Because the
agent has no tools, every ACS gate is an **output-semantic annotator gate** over
the reply text (the azure_doc_qa / prose-failure pattern).

**Next step.** Triage which risks to measure now, then generate one atomic ASSERT
`eval_config.yaml` per selected risk, run a baseline, govern with an output
annotator, and re-measure the delta.
