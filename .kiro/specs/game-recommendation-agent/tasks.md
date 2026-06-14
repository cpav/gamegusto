# Implementation Plan: GameGusto (game-recommendation-agent)

## Overview

The backend and a runnable **headless conversation app** are complete: project setup → locked data contract → core services (ErrorHandler, Bedrock base model via Converse, DynamoDB-backed memory, Tavily) → record sources (Gmail + manual) → agent layer (mood, time, library assembly, recommender, orchestrator) → application wiring (`bootstrap.build_app`) and a CLI (`cli.py`). The library is filled from read-only Gmail purchase emails and manual entry, persisted in DynamoDB, enriched via Tavily, and the agent converses with Claude Sonnet (extended thinking) to produce a recommendation with alternatives.

The **only remaining work is the Streamlit UI** (Task 9) and its application entry point (Task 10), followed by a final checkpoint (Task 11). Everything non-UI is done.

Every source produces and every consumer reads the single canonical `GameRecord` (`models/game_record.py`, data contract v2.0.0; provenance `gmail`/`manual`/`enrichment`).

## Tasks

> Tasks 1–8 (backend + headless app) are complete. The only remaining work is the Streamlit UI (Task 9) and its entry point (Task 10), then the final checkpoint (Task 11).

- [x] 1. Project setup and tooling
  - [x] 1.1 Scaffold project structure, dependencies, and configuration
    - Layout: `ui/`, `agent/`, `services/`, `services/sources/`, `models/`, `tests/`, `docs/`, `scripts/`; pinned `requirements.txt` / `requirements-dev.txt`
    - `config.py` (env only): `AWS_REGION`, `BEDROCK_MODEL_ID`, `TAVILY_API_KEY`, `DYNAMODB_TABLE_NAME` (required); `BEDROCK_REASONING_BUDGET_TOKENS`, `GMAIL_*` (optional); `.env.example` documents names only
    - _Requirements: 2.2_
  - [x] 1.2 Configure quality tooling and provision the environment
    - `pyproject.toml` (ruff, mypy, pytest markers `integration`/`e2e`, coverage); `.pre-commit-config.yaml` (ruff + mypy)
    - _Requirements: 10.1_
  - [x] 1.3 Add preflight access-check scripts
    - `scripts/check_access.py` (AWS/Bedrock, Tavily, DynamoDB required; Gmail optional); `scripts/check_llm.py` (one live Converse call, errors loudly)
    - _Requirements: 10.2_

- [x] 2. Source exploration and data contract (Req 2)
  - [x] 2.1 Exploration spike and locked data contract documentation
    - `docs/data-contract.md` (v2.0.0): Gmail per-retailer structure (Nintendo eShop, Microsoft Store) and Tavily fields; include/exclude decisions; dedup key; DynamoDB persistence note
    - _Requirements: 2.1, 2.3, 2.4_
  - [x] 2.2 Define the unified `GameRecord` contract
    - `models/game_record.py`: `GameRecord` (source `Literal["gmail","manual","enrichment"]`) with `dedup_key` and `is_enriched()`; nested `CommunityReview`
    - _Requirements: 2.2, 2.3_
  - [x] 2.3 Define supporting models
    - `models/platform.py`, `models/recommendation.py`, `models/session.py`
    - _Requirements: 6.4, 7.1, 8.1_
  - [x] 2.4 Unit tests for the data models
    - _Requirements: 2.2, 2.3, 6.4_

- [x] 3. Core services
  - [x] 3.1 Implement ErrorHandler
    - `services/error_handler.py`: detail-free messages for `memory`/`tavily`/`gmail`/`llm`/`unknown`
    - _Requirements: 10.1_
  - [x] 3.2 Property test for error sanitization (P22)
    - _Requirements: 10.1_
  - [x] 3.3 Implement BedrockService (Bedrock Converse base model)
    - `services/bedrock_service.py`: `bedrock-runtime` `converse` against `BEDROCK_MODEL_ID` (Claude Sonnet) with extended thinking; `invoke_with_schema` + `invoke_conversational`; raises `BedrockServiceError` on failure (hard dependency, no fallback)
    - _Requirements: 1.2, 7.2, 10.2_
  - [x] 3.4 Implement MemoryService
    - `services/memory_service.py`: records store, `Platform_List` CRUD, sessions, `is_available` for stateless degradation, behind the `MemoryClient` protocol
    - _Requirements: 3.4, 4.2, 5.2, 6.1, 6.2, 6.3, 8.1, 8.2, 10.3_
  - [x] 3.5 Property tests for memory round-trips (P11–P13)
    - _Requirements: 5.2, 6.1, 6.2, 6.3, 8.1, 8.2_
  - [x] 3.6 Implement TavilyService
    - `services/tavily_service.py`: `enrich`/`autocomplete` (>= 3 chars), free-tier rate limiting, cache-first, graceful degradation
    - _Requirements: 3.3, 5.1, 5.2, 5.4, 5.5, 10.4_
  - [x] 3.7 Property tests for Tavily behavior (P7, P21)
    - _Requirements: 3.3, 5.4_

- [x] 4. Record sources (Gmail + manual)
  - [x] 4.1 Define the RecordSource protocol
    - `services/sources/base.py`: `name`, `is_available()`, `fetch_records()` never raises
    - _Requirements: 3.1, 3.5_
  - [x] 4.2 Implement ManualSource
    - `services/sources/manual_source.py`: `source="manual"`, always available
    - _Requirements: 3.3, 3.5_
  - [x] 4.3 Implement GmailSource
    - `services/sources/gmail_source.py`: read-only scope only, known-sender query, contract-fields-only, sanitized failure
    - _Requirements: 3.2, 4.1, 4.2, 4.3, 10.5_
  - [x] 4.4 Property tests for Gmail privacy and scoping (P8–P10)
    - _Requirements: 3.2, 4.1, 4.2, 4.3_
  - [x] 4.5 Unit tests for Gmail parsers and source skipping
    - _Requirements: 3.2, 3.5_

- [x] 5. Checkpoint - services and sources
  - Ensure all tests pass.

- [x] 6. Agent layer
  - [x] 6.1 Implement MoodInterpreter
    - `agent/mood_interpreter.py`: maps free text to mood dimensions; raises on LLM failure; clarifies only when the model reports the mood uninterpretable
    - _Requirements: 1.1, 1.2, 1.3, 10.2_
  - [x] 6.2 Implement TimeParser
    - _Requirements: 1.4, 1.5, 1.6_
  - [x] 6.3 Property tests for mood and time intake (P1–P4)
    - _Requirements: 1.2, 1.3, 1.5, 1.6_
  - [x] 6.4 Implement LibraryService
    - `agent/library_service.py`: precedence Gmail then manual, dedup, cache-first Tavily enrichment, persistence, skip unavailable sources
    - _Requirements: 3.1, 3.4, 3.5, 5.1, 5.2, 8.1_
  - [x] 6.5 Property tests for library assembly (P5, P6)
    - _Requirements: 2.3, 3.1, 3.4, 3.5_
  - [x] 6.6 Implement Recommender
    - `agent/recommender.py`: owned-platform filter, confirmed-availability gate, review ranking, time budget, no-repeat (last 5), review-summary reasoning + model narrative (raises on LLM failure), up to 3 alternatives
    - _Requirements: 5.3, 7.1, 7.2, 7.3, 7.4, 7.5, 8.3, 10.2_
  - [x] 6.7 Property tests for the recommender (P15–P20)
    - _Requirements: 5.3, 7.1, 7.2, 7.3, 7.4, 7.5, 8.3_
  - [x] 6.8 Implement AgentOrchestrator
    - `agent/orchestrator.py`: mood then time then platform gate then recommendation + alternatives; `needs_platforms` on empty `Platform_List`; stateless mode when memory is down
    - _Requirements: 1.1, 1.4, 6.5, 7.1, 7.4, 10.3_
  - [x] 6.9 Property test for the platform gate (P14)
    - _Requirements: 6.5_

- [x] 7. Checkpoint - agent layer
  - Ensure all tests pass.

- [x] 8. Backend wiring, persistence, and headless app
  - [x] 8.1 DynamoDB-backed memory client
    - `services/dynamodb_memory_client.py`: single-table design (`USER#<id>` / `DOC#<key>` / `EVENT#sessions#`), float<->Decimal at the boundary, behind the `MemoryClient` protocol; `scripts/provision_dynamodb.py` to create the table
    - _Requirements: 8.1, 8.2_
  - [x] 8.2 Application wiring
    - `bootstrap.py` (`build_app(config)`): constructs Bedrock, Tavily, DynamoDB-backed MemoryService, sources (Gmail when configured plus manual) in precedence order, LibraryService, Recommender, AgentOrchestrator
    - _Requirements: 3.1, 3.5, 10.2, 10.3, 10.4_
  - [x] 8.3 Headless conversation entrypoint
    - `cli.py`: manual add, Gmail import/refresh, platform management, and a mood then time then recommendation conversation with alternatives
    - _Requirements: 1.1, 1.4, 3.2, 3.3, 6.1, 7.1, 7.4_
  - [x] 8.4 Backend tests
    - DynamoDB round-trips over a fake table; full conversation-flow test over the real agent graph with the network edge faked
    - _Requirements: 8.1, 8.2, 1.1, 6.5, 7.1, 7.4_

- [ ] 9. Retro arcade Streamlit UI (Req 9)
  - [x] 9.1 Implement the retro arcade theme
    - `ui/theme.py`: retro arcade CSS (Press Start 2P, neon/CRT), responsive media query, idempotent injection
    - _Requirements: 9.1, 9.2_
  - [ ] 9.2 Implement UI bootstrap/accessors
    - `ui/bootstrap.py`: build and cache the service graph in session state by delegating to `bootstrap.build_app`; expose `get_orchestrator`, `get_memory_service`, `get_user_id`, `get_autocomplete`; graceful degradation across memory/Tavily/Gmail
    - _Requirements: 3.5, 10.2, 10.3, 10.4_
  - [ ] 9.3 Implement the chat view
    - `ui/chat_view.py`: conversational chat rendering the primary recommendation in a distinct card with a community-review line and alternatives in an expandable section
    - _Requirements: 9.3_
  - [ ] 9.4 Implement the library/dashboard view
    - `ui/library_view.py`: platform manager (add/edit/remove), add/edit game via manual entry + autocomplete writing to the shared store, `GameRecord`s grouped/filterable by platform, recommendation history
    - _Requirements: 3.3, 6.1, 9.4, 9.5_
  - [ ] 9.5 Implement the sidebar
    - `ui/sidebar.py`: connect Gmail + trigger import showing imported count (sanitized errors on failure), and the chat / library view switch
    - _Requirements: 4.1, 9.6, 10.5_
  - [ ] 9.6 Write UI smoke tests
    - `inject_retro_theme` output contains the pixel font and a responsive media query; the recommendation card includes title, reasoning, playtime, genre, availability, and review line
    - _Requirements: 9.1, 9.2, 9.3_

- [ ] 10. Wire the Streamlit application entry point
  - [ ] 10.1 Implement `ui/app.py`
    - Set page config, inject the theme, render the sidebar (view switch + Gmail connect), and route to the chat or library view, building on `ui/bootstrap.py` (Gmail source present only when configured) with graceful degradation
    - _Requirements: 3.1, 3.5, 9.6, 10.2, 10.3, 10.4, 10.5_

- [ ] 11. Final checkpoint
  - Ensure the full gate is green (ruff, mypy, fast tests) and the Streamlit app launches; confirm the chat and library views work end to end.

## Notes

- The single `GameRecord` contract (v2.0.0) is the only owned-game record type; provenance values are `gmail`, `manual`, `enrichment`.
- The LLM (Claude Sonnet via Bedrock Converse with extended thinking) is a hard dependency: failures surface as errors and are never replaced by mock/deterministic content. Memory (DynamoDB) and Tavily degrade gracefully.
- Live integration tests against real Bedrock / DynamoDB / Gmail are optional and require credentials; offline equivalents (fake DynamoDB table, faked network edge) cover these boundaries in the fast suite.
- The headless `cli.py` is the current runnable entrypoint; the Streamlit UI (Tasks 9–10) will reuse `bootstrap.build_app`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["9.2"] },
    { "id": 1, "tasks": ["9.3", "9.4", "9.5"] },
    { "id": 2, "tasks": ["9.6", "10.1"] },
    { "id": 3, "tasks": ["11"] }
  ]
}
```
