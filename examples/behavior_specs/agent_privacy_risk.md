# Agent Privacy Risk Taxonomy
 
The rapid shift from static Large Language Models (LLMs) to autonomous, multi-agent systems exacerbates persistent privacy challenges while introducing new privacy risks. Traditional privacy frameworks, which rely on boundary-based data containment, file-level access controls, and explicit static consent, fail to address the fluid, multi-step reasoning capabilities and persistent memory architectures of modern agents. This taxonomy categorizes and defines the technical vectors of agentic privacy risks.
 
## Agent Privacy Risks
 
### 1. Data Ingestion & Data Processing Risks
 
This category addresses the vulnerabilities arising from how autonomous agents capture, structure, and retain data.
 
**R1.1 Agent ingests data through continuous tracking:** Background agents designed for proactive assistance continuously monitor user environments, keystrokes, application states, ambient audio and other related content (e.g., emails, calendars, etc.). This constant ingestion violates the principle of data minimization, as agents capture a high volume of non-essential personal context. Over time, the processing of this unstructured ambient data allows the system to draw highly intrusive, unauthorized inferences about a user and even potential unexpected profiling of other individuals around the user.
 
**R1.2 Logging of agent trajectories:** Agent trajectories serve as detailed execution logs, tracking the step-by-step sequence of an agent’s inputs, reasoning steps, and tool calls. While trajectories are critical for engineering evaluation, debugging, and reinforcement learning, they inherently contain raw data that can reveal sensitive information about users. Because these logs capture the exact mental model and contextual exposure of the agent, the lack of granular user control and transparent retention policies over trajectories creates a severe data exposure risk.
 
**R1.3 Compounding risks due to persistence across sessions:** To support personalized, long-term interactions, agents rely on persistent memory architectures that span multiple sessions and platforms. This continuous historical record can allow agents to construct more complex, highly sensitive user profiles. Consequently, privacy risks compound over time; data volunteered for a single, low-risk transaction remains accessible, influencing future agent behaviors and decisions across unrelated context boundaries.
 
**R1.4 The failure of traditional PII redaction techniques:** Legacy techniques for privacy preservation, such as regular expressions and heuristic-based redaction pipelines, are designed for highly structured Personally Identifiable Information (PII) like social security numbers or credit cards. These pipelines fail to sanitize semantic PII — subtle, distributed identity signatures embedded in behavioral patterns, writing styles, and metadata. Limitations of traditional PII redaction techniques mean that this “semantic PII” could be used to re-identify users.
 
**R1.5 Adversarial Context and Memory Poisoning:** Agents are often designed to browse the web, read incoming emails, analyze uploaded documents, or interact with third-party software. Attackers can embed hidden, adversarial payloads—such as invisible text, metadata, or structured system commands—in external content. These adversarial payloads can be executed by a user’s agent, potentially enabling new vectors for their sensitive information to be leaked or stolen.
 
**R1.6 Unintended or unexpected data collection:** Agents can collect sensitive data that goes beyond the user's expected scope, purpose, or intention. This can result in significant privacy harms, such as enabling unexpected user profiling, allowing the utilization of personal data in ways the user never intended, and increasing the risk of sensitive data being leaked or stolen.
 
### 2. Data Aggregation, Use & Sharing Risks
 
This category covers risks associated with the downstream synthesis, semantic utilization, and multi-agent sharing of collected information.
 
**R2.1 Mosaic Effect:** The Mosaic Effect occurs when seemingly harmless, fragmented data points located across different platforms and boundaries are combined to infer highly sensitive, private user data by combining seemingly harmless, fragmented data points collected across different platforms and surfaces. Agentic or multi-agentic systems can exacerbate this risk by enabling the collection of a broader set of information about users from unstructured environments. Even when individual agents operate within local privacy boundaries, the collective pipeline can synthesize these disjointed elements to infer undisclosed personal traits, such as underlying medical conditions. This sequential compounding risk is formally analyzed in recent research on [sequential LLM agent pipelines](https://arxiv.org/abs/2603.05520)
 
**R2.2 Excessive data use by Agents:** Autonomous agents require broad access to context to execute complex reasoning paths. However, empirical studies demonstrate that standard autonomous agents are highly prone to the inadvertent use of unnecessary sensitive information during task execution, as detailed in [studies on autonomous agent data leakages](https://arxiv.org/pdf/2503.09780).
 
i. **Beyond File-Level Privacy** : A key driver of this risk is the failure of traditional file-level access controls. Because agents rely on fluid semantic reasoning and cross-surface data aggregation, blocking access to a single file or database is ineffective. The agent can often reconstruct or infer the restricted information through redundant, unprotected, or secondary sources.
 
ii. Unexpected or unanticipated use of sensitive data persisting across sessions.
 
**R2.3 Unintended or Unexpected Data Sharing, and Deletion DisclosureActions:** Because agents operate with broad agency, they risk executing unauthorized or unexpected actions that spill sensitive data due to goal misalignment or adversarial exploitation. This could include unexpected and unauthorized sharing of sensitive information with a third party. These unexpected execution paths are a structural hazard, where agents lack native social boundaries and may disclose highly sensitive, out-of-context personal data to unintended external entities.
 
**R2.4 Agentic Supply Chain Manipulation:** AI agent components can be compromised upstream, such as third-party skill packages, tool descriptions, or Model Context Protocol (MCP) servers — to manipulate the agent's reasoning logic into leaking sensitive data under the guise of normal execution.
 
**R2.5 Unexpected dynamic code execution** : Autonomous agents can be manipulated into generating and running arbitrary code or system commands directly within its hosting environment. An attacker can exploit the agent’s code execution tool (such as a Python REPL or bash environment), completely bypassing LLM-level privacy controls to gain direct, unauthorized read and write access to the host machine, sensitive environment variables, and connected internal networks.
 
**R2.6 Agent actions reveal sensitive information:** Actions taken by an agent in the course of executing a user’s goal may inadvertently allow a third party to infer sensitive information about the user’s traits, activities, or beliefs. This may occur because the agent does not have sufficient context about what information the user would consider sensitive, or because the agent is not able to determine which actions may allow third parties to infer that sensitive information.
 
### 3. Inconsistent Privacy Practices across Agents
 
As multi-agent ecosystems grow, the lack of standardized coordination protocols creates severe policy fragmentation.
 
**R3.1 Fragmented Privacy Behavior in A2A Interactions:** When agents collaborate to fulfill a request, they must share data across boundaries. Currently, there is no unified protocol to negotiate privacy constraints in Agent-to-Agent (A2A) interactions. An agent operating under strict corporate privacy standards may transfer data to a secondary agent that has weak ingestion boundaries or a different understanding of contextual sharing norms. This misalignment results in immediate data leakage across trust zones.
 
**R3.2 Cascading multi agent failures:** Cascading Multi-Agent Failures occur when independent autonomous agents enter uncontrolled, recursive communication loops, passing messages, errors, or feedback to one another without a termination threshold. This infinite exchange exponentially inflates the token context window of each participating agent. As these bloated context windows are synchronized or logged across diverse enterprise systems, they spill private trace histories—which contain sensitive intermediate reasoning, system prompts, and user secrets—across shared networks and centralized logs, exposing confidential enterprise data to unauthorized parties.
 
### 4. Failure of Legacy Consent Systems
 
Static, binary permission gates (e.g., standard browser pop-ups) are structurally incompatible with the dynamic, open-ended reasoning of autonomous agents.
 
**R4.1 Limitations in Inferring and Adhering to Consent:** There is a difference in fidelity between agent actions and users' intent/expectations regarding how their data is collected, used, shared, or could be at risk. To minimize this user friction, agent architectures are shifting toward intent-based alignment, where the model dynamically interprets and refines user privacy preferences based on natural language cues. Because this alignment is probabilistic rather than deterministic, it cannot guarantee that users’ privacy preferences are inferred or adhered to in a provable way. Additionally, it is highly vulnerable to adversarial prompt manipulation, in which attackers exploit this interpretative flexibility to bypass user-established privacy boundaries.
 
**R4.2 Cognitive Overload & Prompt Fatigue:** Attempting to remediate probabilistic consent by introducing manual runtime confirmation gates for every minor action creates cognitive overload. Confronted with a constant stream of permission pop-ups, users quickly experience prompt fatigue. This fatigue leads to habitual, unreflective clicking ("blind approvals"), nullifying the protective utility of the consent mechanism.
 
**R4.3 The Transparency Deficit:** Autonomous agents generate a vast, non-linear sequence of reasoning steps and multi-tool execution paths. The internal opacity of this process makes it extremely difficult for users to inspect how, when, or why their data was accessed. This severe deficit in clear, readable transparency breeds user anxiety regarding corporate manipulation, degrading trust and creating immense brand and regulatory exposure.
 
**R4.4 Manipulation:** Users can be manipulated into revealing sensitive information. This could occur through design decisions in AI tools (e.g., “dark patterns”) that encourage users to relax their security posture. It might also occur by agents using social engineering techniques (including by simulating human-like traits to gain user trust) in order to trick people into revealing credentials, personal identifiable information (PII), proprietary corporate secrets, or other sensitive information.
 
### 5. Accountability and Governance Risks
 
When privacy violations occur, the distributed nature of agentic execution complicates post-incident forensics and liability attribution.
 
**R5.1 Lack of clarity around who is accountable for a given action:** An agentic action is the synthesized result of user instructions, underlying model behavior, system prompts, retrieval context, and dynamic tool outputs. Consequently, when a privacy incident occurs (e.g., unauthorized data exfiltration), isolating the root cause can be exceptionally difficult. It can be unclear how to distribute responsibility among the third party providers of pre-training and fine-tuning data, the model vendor, the application developer, the third-party tool creator, or the user, which can prevent accountability and hinder effective technical remediation.
 
**R5.2 Inadequate interaction logging:** Analyzing and remediating privacy failures can require a comprehensive execution log that tracks historical interactions across multiple ecosystem boundaries. Currently, there is no standardized, scalable, and privacy-preserving method to implement such cross-ecosystem logging. Without these standardized forensic trails, identifying the exact point of failure during complex multi-agent execution can be difficult.
 
**R5.3 Unauthorized or unexpected disclosure of user data to a third party:** An inappropriate third-party access to sensitive data collected or generated by agents. For example, a cyberattacker or government could obtain access to user logs or agent execution traces, which provide highly concentrated sources of sensitive information about a user that previously would have been spread across multiple online platforms.
 
## Agent Privacy Risk Assessment
 
Agent privacy risk should be assessed as a causal chain:
 
 Deployment condition or threat → failure mechanism → privacy event → affected party → privacy impact → downstream harm
 
| **Stage** | **Node** | **Description** | **Assessment Evidence Type** |
| --- | --- | --- | --- |
| **1** | **Deployment Condition / Threat** | Architectural feature, user behavior, system failure, or adversarial trigger creating the opportunity for an incident. | **Directly Observed**: System design specs, security logs, or threat intelligence). |
| **2** | **Failure Mechanism** | Agent behavior or control weakness through which the event actually occurs (e.g., prompt injection susceptibility, semantic leakage). | **Directly Observed**: Execution traces, model evaluation benchmarks, or audit logs). |
| **3** | **Privacy Event** | Collection, inference, retention, use, modification, or disclosure of personal data in a manner inconsistent with an applicable purpose, permission, expectation, policy, or contextual norm. | **Directly or indirectly Observed**: Network telemetry, audits and inspection, user feedback or output logs. |
| **4** | **Affected Party** | Identification of specific entities impacted (e.g., primary end users, enterprise clients, or non-consenting 3rd parties). | **Inferred / Identified**: User identity mapping or context boundary analysis. |
| **5** | **Privacy Impact** | Immediate privacy consequences (e.g., loss of confidentiality, unauthorized profiling, or credential exposure). | **Inferred / Evaluated**: Data sensitivity classification and contextual policy breach. |
| **6** | **Downstream Harm** | Tangible consequences resulting from the event (e.g., identity theft, financial loss, legal liability, or reputational damage). | **Inferred / Uncertain**: Requires external evidence beyond system logs to demonstrate actual occurrence. |

The assessment should then identify who is affected, the immediate privacy impact, and any credible downstream harm. A successful attack, agent action, or technical data exposure does not by itself demonstrate that downstream harm occurred. The assessment should state which part of the causal chain was observed and which parts remain inferred or uncertain.
 
### Likelihood
 
Likelihood should only be expressed as a probability when supported by observational data with a defined population, denominator, operating environment, and period of observation. Where such evidence is unavailable, the assessment should characterize the following dimensions separately.
 
**Deployment Exposure:** How often does the assessed product encounter the data, capability, recipient, or operating condition required for the privacy event? (Inherent in ordinary product operation, present only for some users or supported workflows, requires an unusual configuration, action, or sequence).
 
**Conditional Failure Evidence:** When the required conditions are present, under what circumstances does the system produce the privacy event?
 
- **Ordinary use:** Demonstrated in representative, non-adversarial workflows.
 
- **Realistic misuse or attack:** Demonstrated under a credible threat model, defined user or attacker capability, and realistic attack budget (range of budgets).
 
- **Plausible but unconfirmed:** Supported by architecture or prior evidence but not reproduced in the assessed system.
 
### Impact
 
Impact should distinguish the privacy event itself from its immediate and downstream effects. The immediate impact should consider the sensitivity, identifiability, and volume of the data; the degree of control lost; departure from the original purpose or context; number and type of recipients; persistence and reversibility; and the affected party’s ability to detect, contest, correct, or delete the information. Credible downstream harms may include physical, economic, reputational, psychological, autonomy-related, discriminatory, relational, legal, organizational, or societal effects.
 
Impact ratings should reflect effects on affected parties, not only technical compromise, regulatory exposure, or organizational reputation.
 
| **Level** | **Definition** |
| --- | --- |
| **Critical** | The privacy event creates severe or potentially irreversible harm to affected parties, such as threats to physical safety, coercive control, systemic discrimination, loss of major life opportunities, or widespread exposure of highly sensitive data. The impact is extensive, persistent, difficult to remediate, or affects a large or particularly vulnerable population. |
| **High** | The privacy event creates substantial harm or loss of control involving sensitive data, important decisions or relationships, or material effects on employment, health, finances, legal status, safety, reputation, or autonomy. The impact may be difficult to detect, contain, reverse, or remediate. |
| **Moderate** | The privacy event causes a meaningful but contained loss of control, contextual misuse, unwanted inference, or disclosure. Its effects are limited in scale, sensitivity, duration, persistence, or downstream consequence and can generally be mitigated. |
| **Low** | The privacy event involves limited data, sensitivity, recipients, persistence, and deviation from reasonable expectations. Effects are minor, readily detectable, reversible, and unlikely to produce material or continuing harm. |