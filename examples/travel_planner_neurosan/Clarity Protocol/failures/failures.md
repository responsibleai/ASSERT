# Failure Modes

1. **[Fabricated budget verification](failure-01-fabricated-budget-verification.md)** (Critical)
   `optimize_itinerary` calls `validate_budget` with hardcoded literals — `flight_cost=850,
   hotel_cost=770, other_costs=200`. None derives from the searches that just ran; 770 assumes
   seven nights whatever `days` was extracted; 200 has no source in any tool. The verdict is
   `total: 1820` for every trip, to every destination, for every duration, presented to the
   traveller as a verified budget check. Deterministic rather than probabilistic, and the harm
   comes from the framing: a price is a claim one might question, but a number returned by
   `validate_budget` is *verification*. **no mitigation plan**
2. **[Entry requirements for the wrong destination](failure-02-wrong-destination-entry-requirements.md)** (Critical)
   `check_travel_advisories` returns one fixed payload for any region — Japan's 90-day visa
   waiver, Japanese encephalitis, earthquake preparedness — echoing back whatever label it was
   given, so France yields Japan's entry requirements titled "France". Nothing at the call
   boundary is anomalous: the tool is invoked correctly and succeeds. The agent is not
   fabricating; it is faithfully relaying false data, which is why no fidelity check detects it.
   Discovered at a border, where no correction exists, and the harm is inversely distributed —
   the traveller who genuinely needs a visa gets the most confidently wrong answer.
   **no mitigation plan**
3. **[Ungrounded cost figures in the itinerary](failure-03-ungrounded-cost-figures.md)** (High)
   The optimizer states prices no tool returned. It never sees raw tool output — only prose
   summaries — so when "summarize concisely" drops the figures, it fills the gap from priors while
   the itinerary reads as sourced. The prompt says "use tool results only"; the pipeline removes
   the tool results one stage before that instruction has to be obeyed. **no mitigation plan**
4. **[Provenance collapse through the summarization chain](failure-04-provenance-collapse.md)** (High)
   Flights, hotels, and safety each pass through an intermediate summarizing LLM, and only the
   summary reaches the optimizer. Not a root cause but the property that makes every other failure
   both possible and undetectable: by the time the harmful claim is written, the evidence that
   would contradict it was discarded a stage earlier. **no mitigation plan**
5. **[Silent default trip parameters](failure-05-silent-default-parameters.md)** (High)
   `classify_intent` falls back to `Tokyo/Japan/7/3000` on any JSON parse failure, and
   `_as_number` substitutes 7 and 3000 for uncoercible values. A stated $1,200 budget silently
   becomes $3,000. The `region` branch is the dangerous one: it is a second, independent route
   into failure 02 that a region-consistency gate reads as *agreement*, because both values say
   Japan. **no mitigation plan**
6. **[The enforcement layer itself fails](failure-06-enforcement-layer-fails.md)** (High)
   Suppressed advisories that read as "nothing required"; an annotator-name mismatch that silently
   no-ops while reporting enforcement active; decorative marking under a headline figure the
   traveller still believes; over-marking that hedges the itinerary into uselessness; and a
   guarded variant that edits `agent.py` or the shared `_tools.py` and thereby measures a
   different system. **no mitigation plan**

## Cross-Cutting Patterns

**The two Critical failures need different mechanisms, and that is the central design finding.**
Budget fabrication has a structural signature — the arguments to `validate_budget` can be
reconciled against the flight prices, hotel rates, and `days` already in the tool log, so it is
decidable by comparison with no judgement involved. Wrong-destination advisories have no
structural signature at all: the tool is called correctly with the correct region and returns
successfully. That one requires a semantic evaluation of the output. Neither mechanism
substitutes for the other, and a design that implements only one addresses only half the harm.

**The seam was built for this.** `run_pipeline` returns `(itinerary, raw_tool_results)` and its
docstring states the log exists so a governed variant can ground an output gate against exactly
the tool outputs the run produced. The log accumulates through a `contextvars.ContextVar`, so it
is concurrency-safe and needs no monkeypatching. Enforcement should consume that log and change
nothing else — not `agent.py`, which is the baseline under measurement, and emphatically not
`phoenix_auto_trace/_tools.py`, which is shared with other demos.

**Correct data always exists and is always discarded.** Every chain here begins with a tool
returning accurate results — real flight prices, real hotel rates, a real advisory payload — and
proceeds by throwing them away: summarized into prose, ignored in favour of constants, or
answered for the wrong country. No failure originates in retrieval. That is what makes an output
gate grounded in the raw log the natural instrument, and it means grounded alternatives are
almost always available, so regeneration should be preferred to hedging.

**The failure is deterministic, which is rare and useful.** `validate_budget` receives the same
three constants on every run and returns `total: 1820` every time. The baseline is therefore
measuring a property of the pipeline rather than a tendency of the model — and it provides an
unusually reliable sentinel for under-enforcement: a governed run that does not move that number
has a gate that is not firing, not a clean baseline.

**Every fix for failure 02 risks recreating it.** Suppressing an unattributable advisory produces
silence, and silence reads as "nothing required". The requirement to surface visa, safety, and
health information is not satisfied by removing wrong information. Marking-with-redirection is the
only response that satisfies both halves.

**Marking is sufficient for advisories and insufficient for the budget verdict.** The distinction
is where the traveller's belief forms. An entry requirement flagged as unverified loses its
authority. A budget verdict flagged as unverified still appears under a headline "$1,820, within
budget", and the traveller reads the number. Since the verdict's entire harm is the verification
framing, it must not be stated as verified at all rather than stated with a caveat.

**No single number describes success.** Harm reduction and legitimate-planning preservation must
be reported together. The five mock tools return genuinely usable data, so a complete, accurate,
grounded itinerary is achievable — and an itinerary hedged into uselessness sends the traveller to
an unmoderated search engine, which is the same outcome as the failure and harder to notice.
