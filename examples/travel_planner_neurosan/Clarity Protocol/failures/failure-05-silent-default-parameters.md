# Failure: Silent default trip parameters

## Summary

Two fallbacks substitute invented trip parameters without telling anyone.

`classify_intent` wraps its JSON parse in a bare `except json.JSONDecodeError` and falls back to
`{"destination": "Tokyo", "region": "Japan", "days": 7, "budget": 3000}`. If the intent LLM emits
anything unparseable, the pipeline plans a week in Tokyo on a $3,000 budget — for a user who
asked about something else entirely.

`_as_number` substitutes 7 for `days` and 3000 for `budget` whenever the extracted value is
`None`, a bool, or an uncoercible string. Its docstring explains this correctly as a defence
against a mid-conversation crash in `validate_budget`, which is a real concern. The cost is that
a traveller who stated a $1,200 budget can silently have it replaced with $3,000, after which
every downstream budget statement is answering a different question.

Neither fallback is recorded in the output or surfaced to the traveller. The itinerary is
produced with the same confidence either way.

This mode matters mostly because of what it does to the others. The `region` default is a second,
independent route into the wrong-destination advisory failure — and one that a gate comparing
"advisory region" against "requested region" will read as *consistent*, because both say Japan.
The `budget` default makes the budget verdict compare a fabricated total against a fabricated
budget, so even a working grounding check has nothing true to reconcile.

## Failure Chain

1. A traveller states a destination, duration, and budget.
2. The intent LLM is asked to return JSON. It returns malformed JSON, or a null, or a string like
   "$3,000".
   - *Observation:* Requesting raw JSON from an LLM without schema enforcement makes this a
     routine occurrence rather than an exceptional one.
   - *Intervention point (prevention):* Treat an unparseable intent as an unknown parameter rather
     than as a known default.
3. The fallback fires. `destination`, `region`, `days`, `budget` are set to values the traveller
   never supplied.
   - *Observation:* Choosing a *plausible* default is what makes this dangerous. `Tokyo/Japan/7/
     3000` produces an itinerary indistinguishable in form from a correct one; an obviously wrong
     default would be caught immediately.
   - *Intervention point (detection):* Compare the parameters actually used against the
     traveller's request.
4. The pipeline runs normally on the substituted parameters. All five stages succeed.
5. **Branch point — wrong destination.** The traveller receives an itinerary for Tokyo. Usually
   obvious, and the least harmful outcome.
6. **Branch point — wrong region only.** `destination` parses but `region` defaults to Japan. The
   advisory payload now matches the region argument, so a region-consistency check passes while
   the traveller receives Japanese entry requirements for somewhere else. **harm begins**
   - *Observation:* This is the most damaging branch, and the least visible. It defeats the
     obvious implementation of a gate for the wrong-destination mode by making the two values
     agree on a falsehood.
7. **Branch point — wrong budget.** A stated $1,200 becomes $3,000. `validate_budget` compares the
   fabricated 1820 total against the fabricated budget and reports `within_budget: true`. **harm
   begins** — the traveller is told a trip fits a budget that is not theirs.
8. The traveller acts on parameters they never supplied. **harm ends** as the trip resolves.
   - *Intervention point (recovery):* Record which parameters were defaulted so affected
     itineraries can be identified.

## Observations

- **Severity:** High — Real harm through wrong budget and wrong advisory routes, on inputs the
  traveller never supplied and cannot see. Rated below the Critical modes because the most common
  branch (wrong destination) is usually self-evident to the reader, and because the fallback only
  fires on parse failure rather than on every run. Rated above the amplifiers because it
  independently produces harm and, in the region branch, actively defeats a plausible fix for a
  Critical mode.
- **Related failures:** Second route into *Entry requirements for the wrong destination*, and the
  one that breaks a naive region-comparison gate. Corrupts the `budget` input to *Fabricated
  budget verification*, so the grounding check must treat parameter provenance as part of what it
  verifies rather than as trusted context.
- **Variants:**
  - Intent fallback invents the whole trip *(brainstorm)* — `Tokyo/Japan/7/3000`
  - Stated budget silently replaced by default *(brainstorm)* — `_as_number` coercion

## Intervention Points

### Prevention
- Treat unparseable intent as unknown rather than as a plausible default.

### Detection
- Compare the parameters actually used against the traveller's stated request, and treat a
  defaulted `region` as unverified rather than as agreement.

### Mitigation
- State the parameters the plan was built on so the traveller can see a substitution.

### Recovery
- Record which parameters were defaulted so affected itineraries can be identified.

---

## Management Plan

[Not yet developed. Run failure management to develop a plan for this failure mode.]
