# Problem

**What it is.** A bounded, single-turn "career health assessment" LLM agent
that operates on synthetic CV text. It has **no tools** and returns **structured
JSON only**. It performs exactly one of three tasks per call:

- `cv_parsing` — extract stated employment history, skills, qualifications,
  certifications, and target role, each with an exact-span evidence citation.
- `narrative_assessment` — write a short, qualified narrative from pre-supplied
  `VERIFIED_FACTS` and `DIMENSION_SCORES`.
- `cv_quality_evaluation` — score five CV dimensions 0–4 with exact-span evidence.

**Why it is consequential.** The agent produces judgments about a real person's
employability from their CV. If it invents facts, over-claims, discriminates, or
can be manipulated by text embedded in the CV, its output can drive unfair
screening or hiring decisions. The entire behavioral contract lives in one
system prompt — every constraint (extract only stated facts, treat CV text as
untrusted data, stay bounded and qualified, refuse unsupported input, no
protected-attribute influence) is prompt-only and therefore defeasible under
adversarial or ambiguous input.

**Core tension.** The tool is asked to be *useful and specific* about a CV while
simultaneously being *rigorously grounded and bounded*. A helpful-by-default
model resolves that tension by smoothing gaps — inferring seniority, inventing
plausible history, issuing confident verdicts — which is exactly the harm.
