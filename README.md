# GameGusto 🕹️🎯

A retro arcade/pinball-styled game **discovery** assistant. Tell it your mood,
time, and taste and it recommends a new game to buy and play — one you don't
already own, playable on a platform you have. A tool-using Claude Sonnet agent on
Amazon Bedrock drives the conversation, learning your taste from your owned
library (imported from Gmail purchase emails + manual entry, enriched via the
web) and avoiding anything you already have.

## Architecture

`models → services → agent → api → web`. The agent runs a Bedrock Converse tool-use
loop (`agent/runtime.py`) over tools that wrap the services
(`agent/tools.py`): platforms, library, enrichment, web search (with deep page
reads for store deals), persistence.
Memory is a single DynamoDB table; the LLM is a hard dependency while memory and
Brave degrade gracefully. [`docs/v1-spec/`](docs/v1-spec/) holds the v1
requirements and correctness properties, still cited by ~108 code comments;
its UI chapters are historical.

Single-user by design: authentication gates access, but all data lives under
one storage identity. [`docs/adding-users.md`](docs/adding-users.md) explains
what a second person actually costs — and why there is deliberately no sign-up
link.

## Run locally

Requires Python 3.11+ (the deployed Lambda runs 3.13):

```bash
python3.13 -m venv .venv && source .venv/bin/activate   # or: uv venv --python 3.13 .venv
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # fill in the values
```

Dependencies are split by concern, so the deployed API ships only what it
runs. Layer on what you need:

| File | For |
|---|---|
| `requirements.txt` | the agent, its services and the data layer — the core |
| `requirements-api.txt` | FastAPI + uvicorn (the v2 HTTP service) |
| `requirements-gmail.txt` | Gmail import — local only, imported lazily |
| `requirements-dev.txt` | tests, lint, types |

Required env (see `.env.example`): `AWS_REGION`, `BEDROCK_MODEL_ID`,
`DYNAMODB_TABLE_NAME`, `BRAVE_API_KEY` (plus AWS credentials via your profile or
`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`). Gmail is optional — with
`requirements-gmail.txt` uninstalled the source is simply unavailable.

- **CLI:** `python cli.py`
- **HTTP API** (v2 frontend backend; same env, `pip install -r requirements-api.txt`):
  `uvicorn --factory api.main:build --reload --port 8000` — JSON endpoints under
  `/api/*` plus an SSE chat stream at `POST /api/chat`. Single-user for now;
  Cognito JWT identity lands with the v2 deployment.
- **Web client (v2 PWA):** `cd web && npm install && npm run dev` — see
  [`web/README.md`](web/README.md). For frontend work without AWS or Bedrock
  spend, run `python scripts/mock_api.py` instead of uvicorn. The visual
  identity (palette tokens + logo) lives in [`design/`](design/README.md).

## Tests

```bash
pytest          # fast unit + property tests, coverage gate
ruff check . && mypy .
```

## Deploy

The app is a PWA on CloudFront with a streaming FastAPI Lambda behind it, all
described in [`infra/`](infra/README.md). Everything runs as the scoped
`gamegusto-deploy` role — never as admin.

```bash
make deploy        # API (bundle + terraform) and the web client
make deploy-web    # just the PWA: build, sync to S3, invalidate
make url           # print the app URL
```

Install it by opening that URL in Safari: **Add to Home Screen** on iPhone,
**File → Add to Dock** on a Mac. Sign-in is Cognito; the API validates the
token on every request.

Real secrets are never committed: `.env` is git-ignored, and the deployed
Brave key lives in SSM Parameter Store, never in Terraform state.
