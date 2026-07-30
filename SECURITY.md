<!-- BEGIN MICROSOFT SECURITY.MD V0.0.9 BLOCK -->

## Security

Microsoft takes the security of our software products and services seriously, which includes all source code repositories managed through our GitHub organizations, which include [Microsoft](https://github.com/Microsoft), [Azure](https://github.com/Azure), [DotNet](https://github.com/dotnet), [AspNet](https://github.com/aspnet) and [Xamarin](https://github.com/xamarin).

If you believe you have found a security vulnerability in any Microsoft-owned repository that meets [Microsoft's definition of a security vulnerability](https://aka.ms/security.md/definition), please report it to us as described below.

## Reporting Security Issues

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them to the Microsoft Security Response Center (MSRC) at [https://msrc.microsoft.com/create-report](https://aka.ms/security.md/msrc/create-report).

If you prefer to submit without logging in, send email to [secure@microsoft.com](mailto:secure@microsoft.com).  If possible, encrypt your message with our PGP key; please download it from the [Microsoft Security Response Center PGP Key page](https://aka.ms/security.md/msrc/pgp).

You should receive a response within 24 hours. If for some reason you do not, please follow up via email to ensure we received your original message. Additional information can be found at [microsoft.com/msrc](https://www.microsoft.com/msrc).

Please include the requested information listed below (as much as you can provide) to help us better understand the nature and scope of the possible issue:

  * Type of issue (e.g. buffer overflow, SQL injection, cross-site scripting, etc.)
  * Full paths of source file(s) related to the manifestation of the issue
  * The location of the affected source code (tag/branch/commit or direct URL)
  * Any special configuration required to reproduce the issue
  * Step-by-step instructions to reproduce the issue
  * Proof-of-concept or exploit code (if possible)
  * Impact of the issue, including how an attacker might exploit the issue

This information will help us triage your report more quickly.

If you are reporting for a bug bounty, more complete reports can contribute to a higher bounty award. Please visit our [Microsoft Bug Bounty Program](https://aka.ms/security.md/msrc/bounty) page for more details about our active programs.

## Preferred Languages

We prefer all communications to be in English.

## Policy

Microsoft follows the principle of [Coordinated Vulnerability Disclosure](https://aka.ms/security.md/cvd).

<!-- END MICROSOFT SECURITY.MD BLOCK -->

## ASSERT security model

The section below is specific to this project. It describes intended, documented behaviour —
not vulnerabilities — so that operators can make an informed decision before running ASSERT.

**An ASSERT eval config is executable content.** `target.callable`, `target.connector` and
`target.tools.module` cause ASSERT to import and execute the Python module you name,
in-process, with your full user privileges — including access to your `.env` credentials.
Importing a module runs its top-level code, so execution begins before any evaluation does.
The guard in front of the import rejects a small set of path substrings; it is a hygiene
filter, not a sandbox. Treat a config file exactly as you would treat source code: only run
configs you wrote or have reviewed.

**Evaluation artifacts may contain sensitive data.** `inference_set.jsonl` records full
transcripts, and — when OpenTelemetry trace capture is enabled — tool arguments and tool
results verbatim. `test_set.jsonl` is by construction a corpus of working adversarial
prompts against your system. Handle both accordingly, and prefer a short retention window.

**The local viewer is unauthenticated.** It binds `127.0.0.1` and must stay there. Do not
expose it with `--host`, port-forwarding, or on a shared machine.

**Supported deployment.** A single-tenant developer workstation. ASSERT is not currently
designed to run as a shared or hosted multi-user service. On a shared build agent, a config
arriving in a pull request would execute on that agent.

If you believe you have found a vulnerability that goes beyond the behaviour described here,
please report it through MSRC as described above rather than in a public issue.

