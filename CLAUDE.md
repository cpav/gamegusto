# Working on GameGusto

A tool-using Claude Sonnet agent on Bedrock that recommends a game to buy and
play tonight — one you don't own, on a platform you have. Deployed as an
installable PWA.

`models → services → agent → api → web`. Dependencies point one way: `api` may
use `agent`, `agent` may use `services`, everything may use `models`. Lower
layers never import from higher ones.

## Environment

Python 3.13 via `uv` (the venv has **no `pip`** — use `uv pip`). Node and
Terraform are installed self-contained and are **not on `PATH` by default**:

```bash
export PATH="$HOME/.local/nodejs/bin:$PATH"   # node/npm
~/.local/bin/terraform                        # terraform
```

On this Intel Mac `cryptography` has no wheel for the pre-commit hook's
isolated interpreter — never add `PyJWT[crypto]` to `additional_dependencies`,
only plain `PyJWT`.

Requirements are split by concern: `requirements.txt` (core), `-api`, `-gmail`
(local only, imported lazily), `-dev`. Pin exact versions.

Secrets come from the environment via `config.py`; the deployed Tavily key
lives in SSM and is fetched by the Lambda entrypoint, never by Terraform.
Never echo a secret value.

## The gate

```bash
make check     # ruff format --check, ruff check, mypy, pytest (85% floor)
```

Green before every PR. Coverage is a floor, not a goal — assertions that mean
something beat a padded number.

## Git

`main` is protected and always green; everything reaches it through a PR.

- Branch `<type>/<kebab-description>`: `feat/`, `fix/`, `refactor/`, `test/`,
  `chore/`.
- Conventional Commits: `<type>(<scope>): <summary>`.
- Commit only when asked. Stage specific files. Never commit `.env` or secrets.
- Prefer new commits over amending pushed history.
- PR body: what changed, why, how it was verified, known gaps.

## Code

- **No dead code, no speculative code.** Build what the task needs. Delete
  what stops being used, including comments that reference deleted files.
- Type hints on everything; `mypy` clean. Avoid `Any` outside untyped
  third-party boundaries — parse into a dataclass there.
- Docstrings say *why*, not what. Comment non-obvious decisions only.
- External failures degrade gracefully and never surface stack traces, keys or
  endpoints — route through the `ErrorHandler` sanitizer.

## Tests

Fast unit + property tests everywhere; `@pytest.mark.integration` and
`@pytest.mark.e2e` are excluded from the default run. Hypothesis covers the
correctness properties (P1–P22 in the spec). Fakes must mirror real behaviour
including its failure modes — a fake that always succeeds cannot exercise the
path the UI reports on.

## Constraints that will bite you

These are load-bearing. Each was found the hard way.

- **Streaming must survive every hop.** API Gateway buffers and Mangum returns
  a single buffered response — neither is used. The API is a Lambda Function
  URL in `RESPONSE_STREAM` mode behind the Lambda Web Adapter, and CloudFront
  compression is off on `/api/*`. The service worker never intercepts
  `/api/*` at all.
- **`Authorization` is not ours.** CloudFront's OAC puts its own SigV4
  signature there, so the app's token travels as `X-Id-Token`, and every POST
  must carry an `x-amz-content-sha256` payload hash.
- **Auth ≠ partitioning.** `current_user` verifies the Cognito token then
  returns `ctx.user_id` (`"default"`), discarding the `sub`. Every user shares
  one library. Read [`docs/adding-users.md`](docs/adding-users.md) before
  changing this — the one-line version silently hides the existing library.
- **Terraform runs as `gamegusto-deploy`**, which cannot touch the live
  DynamoDB table beyond `DescribeTable`, nor edit its own policy. Bootstrap
  changes need admin. After any policy change run
  `infra/bootstrap/verify_policy.py`.
- **The live table is never Terraform-managed.** It holds the only
  irreplaceable data in the system.

## Where things are documented

- [`README.md`](README.md) — run and deploy.
- [`infra/README.md`](infra/README.md) — the IAM model and why it holds.
- [`docs/adding-users.md`](docs/adding-users.md) — multi-user, honestly costed.
- [`docs/data-contract.md`](docs/data-contract.md) — the `GameRecord` contract.
- [`design/README.md`](design/README.md) — palette, logo, type.
- [`.kiro/specs/game-recommendation-agent/`](.kiro/specs/game-recommendation-agent/)
  — the v1 requirements/design. **Still load-bearing:** ~107 code comments
  cite `Req X.Y` from `requirements.md`. Its UI chapters describe the retired
  Streamlit app; the v2 stack is documented in the files above.
