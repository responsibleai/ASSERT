# ACS guardrails example

This offline demo shows the ASSERT→ACS loop without network calls or credentials: synthetic ASSERT findings become an ACS policy, the policy is validated against the known-bad example, and a tiny callable target is re-run through `guard_target(...)`.

## Prerequisites

- The ACS extra: `pip install -e ".[acs]"`.
- The Open Policy Agent (`opa`) binary on your `PATH`. The validate and guard
  steps evaluate the generated Rego through the ACS runtime, which shells out to
  OPA. The Python extra does not install it; see
  https://www.openpolicyagent.org/docs/latest/#running-opa for install options.

## Run

```bash
pip install -e ".[acs]"
python examples/acs_guardrails/demo.py
```

The demo writes a generated policy under `artifacts/acs/acs-guardrails-demo/` and prints:

```text
Generated ACS policy
Validated known-bad examples: handled=1/1
Benign call passed
Violation blocked
PASS
```

## Adapt to a real ASSERT run

After running an eval, generate and validate with your real run and model:

```bash
assert-ai acs generate --suite <suite> --run <run> --out artifacts/acs/<suite> --model azure/gpt-5.4
assert-ai acs validate --manifest artifacts/acs/<suite>/manifest.yaml --suite <suite> --run <run> --fail-on-allow
```

Set provider credentials with environment variables such as `AZURE_API_KEY` and `AZURE_API_BASE`; never print or commit their values. See [Securing agents with ACS](../../docs/guides/securing-agents-with-acs.md) for the full guide.
