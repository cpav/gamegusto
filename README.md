# GameGusto 🕹️🎯

A retro arcade/pinball-styled game **discovery** assistant. Tell it your mood,
time, and taste and it recommends a new game to buy and play — one you don't
already own, playable on a platform you have. A tool-using Claude Sonnet agent on
Amazon Bedrock drives the conversation, learning your taste from your owned
library (imported from Gmail purchase emails + manual entry, enriched via the
web) and avoiding anything you already have.

## Architecture

`models → services → agent → ui`. The agent runs a Bedrock Converse tool-use
loop (`agent/runtime.py`) over tools that wrap the services
(`agent/tools.py`): platforms, library, enrichment, web search (with deep page
reads for store deals), persistence.
Memory is a single DynamoDB table; the LLM is a hard dependency while memory and
Tavily degrade gracefully. See [`.kiro/specs/game-recommendation-agent`](.kiro/specs/game-recommendation-agent)
for the full requirements/design.

## Run locally

Requires Python 3.11+ (the deployment runs 3.13 — see `runtime.txt`):

```bash
python3.13 -m venv .venv && source .venv/bin/activate   # or: uv venv --python 3.13 .venv
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # fill in the values
```

Required env (see `.env.example`): `AWS_REGION`, `BEDROCK_MODEL_ID`,
`DYNAMODB_TABLE_NAME`, `TAVILY_API_KEY` (plus AWS credentials via your profile or
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`). Gmail is optional.

- **CLI:** `python cli.py`
- **Web UI:** `streamlit run streamlit_app.py`
- **HTTP API** (v2 frontend backend; same env, `pip install -r requirements-api.txt`):
  `uvicorn --factory api.main:build --reload --port 8000` — JSON endpoints under
  `/api/*` plus an SSE chat stream at `POST /api/chat`. Single-user for now;
  Cognito JWT identity lands with the v2 deployment. The v2 visual identity
  (palette tokens + logo) lives in [`design/`](design/README.md).

## Tests

```bash
pytest          # fast unit + property tests, coverage gate
ruff check . && mypy .
```

## Deploy to Streamlit Community Cloud (private, phone-friendly)

The hosted app runs without Gmail (your token stays local) on the existing
DynamoDB library + manual entry.

1. **AWS access key** — the app needs an access key for the least-privilege
   `gamegusto` IAM user (Bedrock `InvokeModel` + DynamoDB on the `gamegusto`
   table). It can't use a local AWS profile.
2. **Create the app** — at [share.streamlit.io](https://share.streamlit.io),
   *New app* → repo `cpav/gamegusto`, branch `main`, **Main file path
   `streamlit_app.py`**. Under *Advanced settings* **pick Python 3.13** (a
   pinned `runtime.txt` requests this too). **Do not use Python 3.14** — its
   `dataclasses` changes break `from __future__ import annotations` and the app
   fails to start with `AttributeError: 'NoneType' object has no attribute
   '__dict__'`. Python version can only be set at deploy time, so to change it on
   an existing app, delete it and redeploy (the subdomain is freed for reuse).
3. **Secrets** — open the app's *Settings → Secrets* and paste the keys from
   [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example) with real
   values (`AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   `BEDROCK_MODEL_ID`, `DYNAMODB_TABLE_NAME`, `TAVILY_API_KEY`).
4. **Make it private** — *Settings → Sharing* → restrict to specific viewers and
   invite your Google email. Only allow-listed accounts can open it.
5. **Use it** — open the app URL on your phone, sign in with the invited Google
   account, and play.

Real secrets are never committed (`.env` and `.streamlit/secrets.toml` are
git-ignored).
