---
inclusion: always
---

# Environment Setup

Before writing or running any code, ensure the development environment is provisioned. This project is Python-based (Streamlit, boto3 for Bedrock + DynamoDB, Tavily, pytest/hypothesis).

## First-time setup (run once per machine)

1. Use a Python virtual environment at the repo root, on **Python 3.13** (the version the
   deployment runs; 3.11 is the minimum — see `requires-python`). Never install into the
   system interpreter; macOS's bundled `/usr/bin/python3` is an EOL 3.9 and must not be used.
   `uv` manages the interpreter and venv on this machine:
   ```bash
   uv venv --python 3.13 .venv
   source .venv/bin/activate   # macOS/Linux (zsh)
   ```
2. Install runtime and dev dependencies:
   ```bash
   uv pip install -r requirements.txt -r requirements-dev.txt
   ```
   (`pip install` works identically in a non-uv venv. On Intel macOS, add
   `--only-binary cryptography` — the newest cryptography no longer ships x86_64 wheels.)
4. Install the pre-commit hooks (see git-workflow steering):
   ```bash
   pre-commit install
   ```

## Dependency management rules

- **Two requirement files:**
  - `requirements.txt` — runtime deps (streamlit, boto3, requests, tavily client, etc.)
  - `requirements-dev.txt` — tooling (pytest, pytest-cov, hypothesis, ruff, mypy, pre-commit)
- **Pin versions.** Use exact pins (`package==x.y.z`) so installs are reproducible. Do not use open-ended ranges.
- When adding a new dependency, add it to the correct file with a pinned version and mention it in your change summary. Prefer well-known, actively maintained packages; flag anything unusual.
- After changing dependencies, re-run the install step and verify the app and tests still run.

## Secrets and configuration

- All credentials (AWS region/access for Bedrock + DynamoDB, Tavily API key, optional read-only Gmail OAuth) load from environment variables via `config.py`. Never hardcode secrets. The AWS region is `eu-north-1`.
- Keep a `.env.example` documenting every required variable (names only, no values). The real `.env` must be git-ignored.
- Never read back or echo secret values in logs, responses, or commits.

## Verification

Before considering the environment ready, confirm:
- `python -c "import streamlit, boto3"` succeeds inside the venv
- `pytest --version`, `ruff --version`, and `mypy --version` all resolve
