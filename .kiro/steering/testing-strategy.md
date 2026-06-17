---
inclusion: always
---

# Testing Strategy

Testing approach for this project, layered to match the architecture. The aim is confidence without wasted effort: fast unit/property tests everywhere, integration and end-to-end tests where external boundaries and user flows justify the cost.

## Tooling

- **Test runner:** `pytest`
- **Property-based testing:** `hypothesis` — used to validate the correctness properties (P1–P22) defined in `design.md`.
- **Coverage:** `pytest-cov`.
- Mock external services (Bedrock, DynamoDB, Tavily, Gmail) in unit/integration layers; reserve real network calls for explicitly-marked tests.

## Test layers — when to use each

### Unit tests (always)
- Cover every module in `models`, `services`, `agent`, and `ui` logic.
- Pure logic (time parsing, platform filtering, review ranking, error sanitization, rate limiting) must be unit tested directly.
- Fast, no network, no AWS — mock all service clients.

### Property-based tests (for the correctness properties)
- Each property in `design.md` (P1–P22) gets a Hypothesis test, mapped via the test sub-tasks in `tasks.md`.
- Use these for invariants: parsing round-trips, "every recommendation is playable on an owned platform," ranking monotonicity, CRUD round-trips, rate-limit compliance, error-message sanitization.

### Integration tests (at service boundaries)
- Write integration tests where a component coordinates with a real external contract: Bedrock Converse (tool-use + extended thinking), Tavily search/availability/review parsing, read-only Gmail OAuth + message retrieval, DynamoDB memory store/retrieve.
- Mark them with `@pytest.mark.integration`. They may hit real services and require credentials, so they are **not** part of the default fast run.
- Run integration tests before opening a PR that touches a service client or its parsing logic.

### End-to-end tests (for full user journeys)
- Exercise the complete agent-driven conversation: a free-text request drives the tool-use loop (intake → tool calls for platforms/library/enrichment → recommendation → follow-ups such as "I already played it" or "something shorter"), with services mocked at the network edge.
- Mark with `@pytest.mark.e2e`.
- Required before merging changes to the agent runtime / tool registry, the recommendation logic, or the UI wiring — i.e. anything that changes the user-visible flow.

## Coverage policy

- **Minimum line coverage: 85%**, enforced in CI via `pytest --cov --cov-fail-under=85`.
- New code in `services` and `agent` (the core logic) should aim for ~90%+.
- Coverage is a floor, not a goal — a green number with weak assertions is not acceptable. Prefer meaningful assertions over padding.
- UI rendering code that is hard to unit test should still have at least smoke tests (e.g. theme CSS injection, card content assembly).

## Pytest markers

Register markers in `pyproject.toml` / `pytest.ini`:
```ini
[pytest]
markers =
    integration: tests that exercise real external services (need credentials)
    e2e: end-to-end conversational flow tests
```
Default fast run excludes the slow layers:
```bash
pytest -m "not integration and not e2e" --cov --cov-fail-under=85
```

## When writing a feature

1. Write/extend unit tests alongside the code.
2. Add or update the relevant property test if the change touches a correctness property.
3. Add integration/e2e coverage if you touched a service boundary or the user flow.
4. Ensure the full gate (lint, types, coverage threshold) is green before opening a PR.
