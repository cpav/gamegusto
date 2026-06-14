---
inclusion: always
---

# Environment Setup

Before writing or running any code, ensure the development environment is provisioned. This project is Python-based (Streamlit, boto3/AgentCore, Tavily, pytest/hypothesis).

## First-time setup (run once per machine)

1. Use a Python virtual environment at the repo root. Never install into the system interpreter.
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # macOS/Linux (zsh)
   ```
2. Upgrade packaging tools inside the venv:
   ```bash
   python -m pip install --upgrade pip
   ```
3. Install runtime and dev dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```
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

- All credentials (AWS, Tavily API key, Xbox client ID/secret) load from environment variables via `config.py`. Never hardcode secrets.
- Keep a `.env.example` documenting every required variable (names only, no values). The real `.env` must be git-ignored.
- Never read back or echo secret values in logs, responses, or commits.

## Verification

Before considering the environment ready, confirm:
- `python -c "import streamlit, boto3"` succeeds inside the venv
- `pytest --version`, `ruff --version`, and `mypy --version` all resolve
