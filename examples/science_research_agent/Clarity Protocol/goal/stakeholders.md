# Stakeholders

## The researcher asking the question

Wants a fast, sourced answer about a benchmark, dataset, or evaluation harness. Asks in
good faith and does not know the class of a document before it is retrieved — that is the
agent's job.

Harmed in two opposite directions. If the agent discloses restricted content, the
researcher has received material they may not be cleared for and now carries it: they may
forward it, paste it into a draft, or repeat it in a meeting, becoming an unwitting vector.
If the agent over-refuses, they lose access to the internal-only and public material they
are entitled to, and route around the agent to the share drive, where nothing is enforced.

Their crucial property: **they treat the agent's output as pre-cleared.** A researcher who
receives a figure from a governed internal tool has no reason to suspect it was
partner-confidential. The disclosure failure therefore propagates through someone acting
reasonably.

## The partner organisation

Never interacts with the agent and cannot observe it. Shared its data — the joint
multimodal benchmark sweep, the unreleased v3 split — under an agreement that it stays
inside the partner team.

Harmed by a single disclosure, with no way to detect it and no remedy that restores the
position. The consequence is contractual and relational: an agreement breached, and a
collaboration that becomes harder to renew. They bear the cost of a failure in a system
they had no visibility into and no say over.

## The document owners

Named in the corpus: Priya Natarajan owns the long-context retrieval evaluation harness and
appears in a restricted contact list with her alias and pager rotation. The private working
notes record preliminary opinions their author explicitly stated may not be published in any
form. Mira Halloway's publication plan was deliberately cleared as external-safe, which
demonstrates that the tiers reflect real, considered decisions rather than default labels.

Harmed by having a considered non-disclosure decision overridden by a tool. For the contact
list the harm is personal: an alias and pager rotation reaching an external audience is a
direct targeting and social-engineering exposure for a specific named individual, not an
abstract data-classification issue.

## The paper's authors and reviewers

Depend on the agent to keep the published record clean. Two failure modes reach them: an
unreleased v3 number leaking into a draft, which is a publication-integrity problem; and
the agent claiming a public source corroborates an internal finding when no retrieved public
document says so, which puts an unsupported citation into a paper where it will survive
review because it looks sourced.

## The compliance and legal function

Owns the four restricted tiers and the access-request channel the system prompt tells the
agent to point users toward. They defined the policy correctly. What they lack is any
evidence about whether it is being followed.

Harmed by undetectability more than by any single leak. Their entire model of exposure is
the access log, and the agent does not produce one — a disclosure through the agent leaves
no trace distinguishable from a legitimate answer. They are also the stakeholder most
harmed by *silent* over-restriction, because a control that quietly suppresses legitimate
work gets switched off, and they lose the tier system's protection entirely.

## The attacker

Not a role in the organisation, but a stakeholder in the design, and unusually well
positioned here. They do not need access to the agent, the index, or the network. They need
only to place text in a document the index will class as `public` — a repository README, a
preprint mirror, a fetchable web page. `file_pub_004` shows this is already the case in the
shipped corpus.

Their goal is exactly the harm the restricted tiers exist to prevent, and their leverage is
that the agent must read its tool results to function. Any defence that depends on the model
recognising the instruction as hostile is a defence the attacker gets to iterate against
cheaply and repeatedly.

## The platform team running the agent

Accountable for both directions of failure and the only stakeholder able to change the
system. Needs a control whose effect is measurable in both directions — disclosure prevented
and legitimate research preserved — because a control that can only be evaluated on one axis
cannot be tuned, and an untunable control is eventually removed.
