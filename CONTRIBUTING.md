# Contributing to ASSERT

Thank you for your interest in contributing to ASSERT. This project is in customer preview, so the fastest way to help is to keep changes small, reviewable, and tied to a specific issue or customer-facing problem.

## Before you start

- Check the open issues for the problem you want to fix.
- For customer-facing fixes, make sure the issue describes the user problem, expected behavior, and validation plan.
- Keep pull requests focused. Prefer one small fix per pull request over one broad cleanup.
- Do not include secrets, customer data, internal service names, or private preview artifacts in commits, issues, or pull requests.

## Development setup

ASSERT requires Python 3.11 or later. Node.js is needed only for viewer work and tests that exercise viewer assets.

```bash
git clone https://github.com/responsibleai/ASSERT.git
cd ASSERT
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph,dev]"
cp .env.example .env
```

Windows PowerShell equivalent:

```powershell
git clone https://github.com/responsibleai/ASSERT.git
cd ASSERT
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[otel,langgraph,dev]"
Copy-Item .env.example .env
```

If you are changing the viewer or running tests that import viewer packages, install viewer dependencies too:

```bash
npm ci --prefix viewer
```

## Common validation commands

Run the smallest validation that covers your change, and include the command output in the pull request description.

```bash
python -m pytest tests/test_model_client.py -q
python -m pytest tests/ -x -q
```

For docs-only changes, validate links and examples manually. If you change setup instructions, test them in a clean virtual environment when practical.

## Pull request checklist

Before opening a pull request:

- Link the issue or workback item the pull request addresses.
- Explain the customer-facing problem or release-checklist item.
- Describe the expected behavior after the change.
- Include validation evidence.
- Keep generated artifacts, local run outputs, `.env` files, and credentials out of the diff.
- Call out any follow-up work that is intentionally not included.

## Code and documentation guidelines

- Match the existing project style and terminology.
- Prefer clear user-facing errors over provider-specific stack traces.
- Keep preview-language honest. Do not imply that ASSERT certifies safety, compliance, or production readiness.
- For docs, distinguish current YAML keys from intended developer-facing terminology when both matter.
- For examples, use synthetic data only.

## Security issues

Do not report security vulnerabilities through public GitHub issues. Follow the reporting guidance in [`SECURITY.md`](SECURITY.md).

## Contributor License Agreement

Most contributions require agreement to a Contributor License Agreement (CLA). When you open a pull request, the CLA bot will indicate whether any action is required. For details, see <https://cla.opensource.microsoft.com>.

## Code of Conduct

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For questions or concerns, see [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
