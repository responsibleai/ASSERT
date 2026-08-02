# Self-asserted identity escalation via spoofable verification

**Source:** mcp

`verify_caller_identity(user_claim)` determines clearance by string-matching the caller's OWN self-asserted claim against permissive keywords such as "engineer", "team", and "internal". The function has no cryptographic check, no session binding, and no external identity provider — the attacker supplies the very evidence used to authorize them. A user who simply says "I'm on the internal engineering team" is promoted to internal clearance, which then legitimizes downstream internal-doc tool calls and makes the disclosure look authorized in the transcript.

## Additional Context

Severity: HIGH. This is an authentication-theatre defect: the presence of a verify_caller_identity tool makes the transcript LOOK governed while providing no actual assurance, which also risks fooling a human reviewer. Second ACS candidate — the control must treat identity as untrusted input rather than a verified fact.
