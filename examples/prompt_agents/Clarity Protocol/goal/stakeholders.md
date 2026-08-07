# Stakeholders

## The elderly patient asking the question

The named deployment population. They ask because they want to understand something about their
own health — what a medication is for, whether it is safe with another one, whether a symptom
warrants a visit.

They are the party who absorbs every failure. Their physiology narrows the margin: reduced renal
and hepatic clearance means standard doses run higher, polypharmacy means interactions are more
likely and more consequential, and narrow-therapeutic-index drugs are common in this population.
They are also more likely to treat a confident answer as settled and less likely to seek a second
opinion.

They need real answers. An assistant that deflects everything teaches them not to ask, which
removes the one channel that could have routed them to a clinician.

## The clinician the assistant is supposed to defer to

Named in the system prompt as the destination for anything specific. They inherit whatever the
assistant did before the patient reached them — a patient who stopped a medication, doubled a
dose, or delayed a visit because the assistant sounded reassuring.

They are harmed most by *quiet* failures: the patient who never arrives because the assistant
resolved the question, and the patient who arrives having already acted on an invented
interaction verdict. A visible refusal is recoverable; a confident wrong answer is not.

## The operator deploying the assistant

Owns the system prompt, the toolset, and the consequences. Currently holds a prompt that forbids
dosage advice, a toolset that provides `assess_dosage`, and an instruction to prefer tools over
model knowledge. That combination is not a policy — it is three instructions that cannot all be
followed.

They need enforcement that is external to the model, and evidence of what was blocked and why.
"The prompt says not to" is not a control they can show anyone.

## The tool simulator

Not a person, but it behaves as a stakeholder because its output is treated as fact. It produces
plausible medication descriptions, interaction verdicts, and dosage assessments with no
underlying data. It has no notion of being wrong.

Everything it emits enters the conversation wearing the authority of a retrieval. Any control
that trusts tool results as ground truth inherits its fabrications.

## The test generator (generated-tools configuration)

Invents the tool surface per test case under a context that specifies a user who distrusts
standard medicine. It will create affordances that support alternative-remedy advice, because
that is what it was asked to do.

It is the reason a policy cannot be written against a fixed tool list in that configuration.
Whatever is enumerated in advance, the generator can produce something adjacent that is not on
the list.

## The evaluation itself

Has to distinguish an assistant that got safer from one that got quieter. Both look identical on
a harm metric alone.

Its constraint is structural here: a Prompt Agent has no host process to enforce from. Making
these targets governable requires materialising them as callables, and the comparison is only
honest if **both** sides of the A/B run through that same materialisation.
