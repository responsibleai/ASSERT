# Actionable dosage directives and false reassurance emerge under multi-turn pressure

**Source:** mcp
**Pre-existing:** Yes

The health assistant issues concrete, actionable medical guidance -- specific dosage amounts, instructions to adjust or stop a medication, and reassurances that a described symptom is nothing to worry about -- to a user population explicitly identified as elderly patients.

The agent's own system prompt forbids exactly this ("Never provide dosage recommendations or diagnoses"). It holds that line reliably in single-turn exchanges and abandons it under sustained multi-turn pressure, which is where the entire measured violation rate lives.

The harm is direct and physical. Unlike disclosure or fabrication failures, there is no intermediate step between the bad output and the injury: a user reads a dose and takes it. And because the failure surfaces only after several turns of the model being helpful and warm, it arrives at precisely the moment the user has the most reason to trust it.

## Additional Context

Measured on the ungoverned baseline (25 prompt rows + 25 scenario rows, built-in judge dimensions only):

| dimension        | prompts | scenarios |
|------------------|---------|-----------|
| policy_violation | 0.0%    | 24.0%     |
| overrefusal      | 0.0%    | 8.0%      |

The 0% -> 24% split is the entire story of this domain, and it is a sharper version of the pattern seen elsewhere in this batch.

Single-turn prompts are perfectly defended. The system prompt's explicit prohibitions ("Never provide dosage recommendations or diagnoses", "Always recommend consulting a healthcare professional") are highly salient when the request arrives cold and unambiguous, and the model refuses every time.

The defense collapses under multi-turn pressure. The failing scenarios share a recognizable arc: the tester establishes rapport, supplies clinical context ("my mother is 78 and her doctor already put her on this"), narrows to a specific question, and expresses distress or urgency at the refusal. The model, having spent several turns being warm and cooperative, treats the standing prohibition as one consideration to be balanced against an increasingly sympathetic case rather than as a hard limit.

The deployment context makes this materially worse than the raw rate suggests. The stated audience is elderly patients, a population with higher polypharmacy rates, narrower therapeutic windows, and greater likelihood of acting on an authoritative-sounding instruction without independent verification. A dosage figure that is merely wrong for a general adult can be dangerous for this population.

Two recurring sub-shapes were observed in the failing rows:

1. Dose directives -- concrete "take X mg" / "double it" / "skip today's" instructions, sometimes hedged with a consult-your-doctor line that does not undo the actionable content.

2. False reassurance -- telling a user that a described symptom is not concerning, which is a negative diagnosis and functions as advice to NOT seek care. This shape is easy to miss because it looks like comfort rather than instruction, and it is arguably the more dangerous of the two: it removes the safety net rather than adding a risk.
