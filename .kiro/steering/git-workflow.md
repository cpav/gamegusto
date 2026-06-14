---
inclusion: always
---

# Git Workflow & Branching Strategy

A lightweight trunk-based flow with short-lived feature branches and pull requests. The goal is a clean, documented history where `main` is always releasable.

## Branching

- **`main` is protected and always green.** Never commit directly to `main`. It must always build, lint, type-check, and pass tests.
- **Branch for every unit of work.** Create a short-lived branch off the latest `main`:
  ```bash
  git checkout main
  git pull --ff-only
  git checkout -b <type>/<short-description>
  ```
- **Branch naming:** `<type>/<short-description>` using kebab-case. Types:
  - `feat/` — new functionality (e.g. `feat/platform-manager`)
  - `fix/` — bug fixes
  - `refactor/` — non-behavioral cleanup
  - `test/` — test-only additions
  - `chore/` — tooling, deps, config
- Keep branches small and focused — ideally one task (or sub-task group) from `tasks.md` per branch. Rebase on `main` if it moves ahead.

## Commits

- Use **Conventional Commits**: `<type>(<scope>): <summary>` (e.g. `feat(recommender): filter candidates by owned platform`).
- Commit in logical, working increments. Each commit should leave the code in a runnable state.
- Only create commits when explicitly asked. Stage specific files rather than `git add .`, and never commit `.env`, secrets, or the `.venv`.
- Do not amend or force-push shared/pushed history. Prefer new commits.

## Pull Requests

- Push the branch and open a PR into `main`:
  ```bash
  git push -u origin <branch>
  ```
  Then create the PR with the platform CLI (e.g. `gh pr create`).
- **Every change reaches `main` through a PR** — this keeps work reviewed and documented. No direct pushes.
- PR title: concise (<70 chars), Conventional-Commit style.
- PR description must cover: what changed, why, which `tasks.md` items/requirements it addresses, how it was tested, and any follow-ups or known gaps.
- **A PR may only be opened when the pre-merge gate passes** (see below). State the verification results in the PR description.
- Link the PR to the relevant spec task so the trail from requirement → task → code is traceable.

## Pre-merge gate (must pass before opening/merging a PR)

Run locally (and rely on CI to re-check):
1. `ruff format --check .` and `ruff check .` — formatting + lint clean
2. `mypy .` — type checks clean
3. `pytest` with coverage meeting the threshold (see testing-strategy steering)
4. The Streamlit app imports/launches without error

Do not merge with a red gate. Fix forward on the same branch.

## Hygiene

- Delete merged branches.
- Keep history readable; avoid noisy "wip"/"fix typo" chains — squash when it improves clarity.
- Never rewrite `main` history.
