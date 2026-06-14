# Implementation Plan: GameGusto (game-recommendation-agent)

## Overview

Incremental build of GameGusto in Python: project setup → source exploration & locked data contract → services (errors, Bedrock, memory, Tavily) → record sources → agent (mood, time, library assembly, recommender, orchestrator) → retro arcade Streamlit UI (chat + library views) → integration wiring.

Every source produces and every consumer reads the single canonical `GameRecord` (`models/game_record.py`); there are no per-source record types. Test sub-tasks are marked optional with `*` and consolidate the design's 22 correctness properties (P1–P22) by module. Three checkpoints validate progress at the major seams.

## Tasks

- [x] 1. Project setup and tooling
  - [x] 1.1 Scaffold project structure, dependencies, and configuration
    - Create the `gamegusto/` layout: `ui/`, `agent/`, `services/`, `services/sources/`, `models/`, `tests/`, `docs/`, `scripts/`
    - `requirements.txt` with pinned runtime deps: `streamlit`, `boto3`, `requests`, `tavily-python`, `google-api-python-client`, `google-auth`, `google-auth-oauthlib`
    - `requirements-dev.txt` with pinned tooling: `pytest`, `pytest-cov`, `hypothesis`, `ruff`, `mypy`, `pre-commit`
    - `config.py` loading every env var via environment only (no hardcoded secrets) and `.env.example` documenting names only: `AWS_REGION`, `BEDROCK_AGENT_ID`, `BEDROCK_AGENT_ALIAS_ID`, `TAVILY_API_KEY` (required); `XBOX_CLIENT_ID`/`XBOX_CLIENT_SECRET`, `GMAIL_CREDENTIALS_PATH`/`GMAIL_TOKEN_PATH`/`GMAIL_REDIRECT_URI` (optional)
    - `.gitignore` excluding `.env`, `.venv`, caches
    - _Requirements: 2.2_

  - [x] 1.2 Configure quality tooling and provision the environment
    - `pyproject.toml`: ruff (format + lint), mypy, pytest config with markers `integration` and `e2e`, and `--cov-fail-under=85`
    - `.pre-commit-config.yaml` running ruff + mypy; create `.venv`, install `requirements.txt` + `requirements-dev.txt`, then `pre-commit install`
    - _Requirements: 10.1_

  - [x] 1.3 Add preflight access-check script
    - `scripts/check_access.py` validating required services (AWS/Bedrock, Tavily) as PASS/FAIL and optional services (Xbox, Gmail) as SKIPPED when their env vars are unset
    - _Requirements: 3.6, 5.4_

- [ ] 2. Source exploration and data contract (Req 2)
  - [ ] 2.1 Exploration spike and locked data contract documentation
    - Probe and document, in `docs/data-contract.md`, the real fields exposed by the Xbox platform API, Gmail purchase-email structure per retailer (Nintendo eShop, Microsoft Store), and Tavily response fields
    - For every exposed field record the include/exclude decision; document the normalized title+platform dedup key; lock the versioned `GameRecord` contract
    - _Requirements: 2.1, 2.3, 2.4_

  - [ ] 2.2 Define the unified `GameRecord` contract
    - `models/game_record.py`: `GameRecord` (title, platforms `list[str]`, source `Literal["xbox","gmail","manual","enrichment"]`, purchase_date `date|None`, genre `str|None`, estimated_playtime `int|None`, community_review `CommunityReview|None`, platform_availability `list[str]`, external_ids `dict`) with `dedup_key` property and `is_enriched()`; nested `CommunityReview` (score, sentiment_summary, source_count)
    - This single record replaces any `GameMetadata`/`GameHistoryEntry`/`ExtractedGameRecord` — do not create those
    - _Requirements: 2.2, 2.3_

  - [ ] 2.3 Define supporting models
    - `models/platform.py` (`OwnedPlatform` with free-text `name` + generated `platform_id`); `models/recommendation.py` (`Recommendation` display model); `models/session.py` (`SessionState`, `SessionData`)
    - `models/session.py` uses `from __future__ import annotations` + `TYPE_CHECKING` for `MoodDimensions` so models never import the agent layer at runtime
    - _Requirements: 6.4, 7.1, 8.1_

  - [ ] 2.4 Write unit tests for the data models
    - Cover `dedup_key` normalization (casefold + whitespace strip) and `is_enriched()` truth table; `OwnedPlatform` id generation
    - _Requirements: 2.2, 2.3, 6.4_

- [ ] 3. Core services
  - [ ] 3.1 Implement ErrorHandler
    - `services/error_handler.py`: `ErrorHandler.sanitize_error(exc, service)` returning generic, detail-free messages for `memory`/`tavily`/`xbox`/`gmail`/`unknown`
    - _Requirements: 10.1, 10.4_

  - [ ] 3.2 Write property test for error sanitization
    - **Property 22: Error messages never expose technical details**
    - **Validates: Requirements 10.1, 10.4**

  - [ ] 3.3 Implement BedrockService
    - `services/bedrock_service.py`: `BedrockService` with `invoke_with_schema` and `invoke_conversational` over AWS Bedrock AgentCore
    - _Requirements: 1.2, 7.2_

  - [ ] 3.4 Implement MemoryService
    - `services/memory_service.py`: single store — `get_records`/`store_records`/`upsert_record` for `GameRecord`s with defensive dedup and contract-fields-only persistence; `Platform_List` CRUD; `store_session`/`get_recent_recommendations`; `is_available` for stateless degradation
    - _Requirements: 3.5, 4.2, 5.2, 6.1, 6.2, 6.3, 8.1, 8.2, 9.5, 10.2_

  - [ ] 3.5 Write property tests for memory round-trips
    - **Property 11: Game_Record store round-trip** (Validates: Requirements 5.2, 9.5)
    - **Property 12: Platform_List CRUD round-trip** (Validates: Requirements 6.1, 6.2, 6.3, 6.4)
    - **Property 13: Session persistence round-trip** (Validates: Requirements 8.1, 8.2)

  - [ ] 3.6 Implement TavilyService
    - `services/tavily_service.py`: `enrich(record)` (genre, estimated_playtime, platform availability, community_review) and `autocomplete(query)` active only at ≥3 chars; free-tier rate limiting, cache-first, graceful degradation that returns the record/empty results instead of raising
    - _Requirements: 3.4, 5.1, 5.2, 5.4, 5.5, 10.3_

  - [ ] 3.7 Write property tests for Tavily behavior
    - **Property 7: Autocomplete activation threshold** (Validates: Requirements 3.4)
    - **Property 21: Tavily rate-limit compliance** (Validates: Requirements 5.4)

- [ ] 4. Record sources
  - [ ] 4.1 Define the RecordSource protocol
    - `services/sources/base.py`: `RecordSource` Protocol with `name`, `is_available()`, `fetch_records() -> list[GameRecord]`; contract that `fetch_records` never raises to the caller
    - _Requirements: 3.1, 3.6_

  - [ ] 4.2 Implement ManualSource
    - `services/sources/manual_source.py`: `ManualSource` (`source="manual"`, `is_available()` always `True`) returning user-entered records staged in memory
    - _Requirements: 3.4, 3.6_

  - [ ] 4.3 Implement XboxSource
    - `services/sources/xbox_source.py`: `XboxSource` (`source="xbox"`) with OAuth authentication; maps every retrieved title to a `GameRecord` populating all available metadata; degrades to `[]` on failure
    - _Requirements: 3.2, 10.4_

  - [ ] 4.4 Implement GmailSource
    - `services/sources/gmail_source.py`: `GmailSource` (`source="gmail"`) with `SCOPES=[gmail.readonly]` only, a `KNOWN_SENDERS` registry and per-retailer parser registry, a Gmail query built solely from known senders, retaining only contract fields (discarding raw content), returning `[]` plus a sanitized error on failure
    - _Requirements: 3.3, 4.1, 4.2, 4.3, 10.4_

  - [ ] 4.5 Write property tests for Gmail privacy and scoping
    - **Property 8: Gmail import restricts to known purchase-confirmation senders** (Validates: Requirements 3.3, 4.3)
    - **Property 9: Gmail import retains only contract fields** (Validates: Requirements 4.2)
    - **Property 10: Gmail import requests read-only scope only** (Validates: Requirements 4.1)

  - [ ] 4.6 Write unit tests for Gmail parsers and source skipping
    - Per-retailer parsers turn representative Nintendo eShop / Microsoft Store emails into correct `GameRecord`s; an unavailable source is skipped without error
    - _Requirements: 3.3, 3.6_

- [ ] 5. Checkpoint - services and sources
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 6. Agent layer
  - [ ] 6.1 Implement MoodInterpreter
    - `agent/mood_interpreter.py`: `MoodDimensions`, `MoodInterpretation`, and `MoodInterpreter.interpret` mapping free text to dimensions and flagging clarification when unclear
    - _Requirements: 1.1, 1.2, 1.3_

  - [ ] 6.2 Implement TimeParser
    - `agent/time_parser.py`: `TimeParser.parse` returning minutes for explicit hour/minute expressions and `needs_clarification` for vague input
    - _Requirements: 1.4, 1.5, 1.6_

  - [ ] 6.3 Write property tests for mood and time intake
    - **Property 1: Mood interpretation produces valid dimensions** (Validates: Requirements 1.2)
    - **Property 2: Uninterpretable mood triggers clarification** (Validates: Requirements 1.3)
    - **Property 3: Time budget parsing** (Validates: Requirements 1.5)
    - **Property 4: Ambiguous time triggers clarification** (Validates: Requirements 1.6)

  - [ ] 6.4 Implement LibraryService
    - `agent/library_service.py`: `refresh` runs sources in precedence order Xbox → Gmail → manual, dedups by `dedup_key` (earlier source wins), enriches cache-first via Tavily, persists to memory, and skips unavailable sources
    - _Requirements: 3.1, 3.5, 3.6, 5.1, 5.2, 8.1, 10.4_

  - [ ] 6.5 Write property tests for library assembly
    - **Property 5: Dedup is precedence-aware and key-normalized** (Validates: Requirements 2.3, 3.1, 3.5)
    - **Property 6: Source unavailability does not break assembly** (Validates: Requirements 3.6, 10.4)

  - [ ] 6.6 Implement Recommender
    - `agent/recommender.py`: operate over `GameRecord`s — filter to owned platforms, exclude unconfirmed availability from the primary, rank by community review, respect the time budget, avoid repeats from the last 5 sessions, build reasoning including the review summary, and provide up to 3 alternatives
    - _Requirements: 5.3, 7.1, 7.2, 7.3, 7.4, 7.5, 8.3_

  - [ ] 6.7 Write property tests for the recommender
    - **Property 15: Every recommendation is playable on an owned platform** (Validates: Requirements 5.3, 7.1, 7.4)
    - **Property 16: Unconfirmed availability is never the primary recommendation** (Validates: Requirements 7.5)
    - **Property 17: Community review quality drives ranking** (Validates: Requirements 7.2)
    - **Property 18: Primary reasoning includes a community review summary** (Validates: Requirements 7.3)
    - **Property 19: Time budget constraint on recommendations** (Validates: Requirements 7.1)
    - **Property 20: No repeat recommendations in recent history** (Validates: Requirements 8.3)

  - [ ] 6.8 Implement AgentOrchestrator
    - `agent/orchestrator.py`: `AgentResponse` (incl. `needs_platforms`); drive mood → time → platform gate → recommendation; an empty `Platform_List` blocks recommendation with `needs_platforms=True`
    - _Requirements: 1.1, 1.4, 6.5, 7.1, 10.2_

  - [ ] 6.9 Write property test for the platform gate
    - **Property 14: Empty platform list blocks recommendation**
    - **Validates: Requirements 6.5**

- [ ] 7. Checkpoint - agent layer
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Retro arcade Streamlit UI (Req 9)
  - [ ] 8.1 Implement the retro arcade theme
    - `ui/theme.py`: retro arcade CSS (Press Start 2P font, neon/CRT styling), responsive media queries, idempotent single-injection per session
    - _Requirements: 9.1, 9.2_

  - [ ] 8.2 Implement UI bootstrap/accessors
    - `ui/bootstrap.py`: lazily construct and cache the service graph in session state — `BedrockService`, `MemoryService`, `TavilyService`, sources (Xbox/Gmail built only when env vars set, else omitted), `LibraryService` in precedence order, `Recommender`, `AgentOrchestrator`; expose `get_orchestrator`, `get_memory_service`, `get_user_id`, `get_autocomplete` with graceful degradation
    - _Requirements: 3.6, 10.2, 10.3, 10.4_

  - [ ] 8.3 Implement the chat view
    - `ui/chat_view.py`: conversational chat that renders the primary recommendation in a distinct card with a community-review line and alternatives in an expandable section
    - _Requirements: 9.3_

  - [ ] 8.4 Implement the library/dashboard view
    - `ui/library_view.py`: platform manager (add/edit/remove), add/edit game via manual entry + autocomplete writing to the shared store, `GameRecord`s grouped/filterable by platform, and recommendation history
    - _Requirements: 3.4, 6.1, 9.4, 9.5_

  - [ ] 8.5 Implement the sidebar
    - `ui/sidebar.py`: connect Xbox, connect Gmail + trigger import showing imported count (sanitized errors on failure), and the chat ⇄ library view switch
    - _Requirements: 4.1, 9.6, 10.4_

  - [ ] 8.6 Write UI smoke tests
    - `inject_retro_theme` output contains the pixel font and a responsive media query; the recommendation card includes title, reasoning, playtime, genre, availability, and review line
    - _Requirements: 9.1, 9.2, 9.3_

- [ ] 9. Integration and wiring
  - [ ] 9.1 Wire the application entry point
    - `ui/app.py`: set page config, inject the theme, render the sidebar (view switch + connection controls), and route to the chat or library view; rely on `ui/bootstrap.py` so Xbox/Gmail sources are present only when configured and `LibraryService` receives sources in precedence order, with graceful degradation across memory/Tavily/Xbox/Gmail
    - _Requirements: 3.1, 3.6, 9.6, 10.2, 10.3, 10.4_

  - [ ] 9.2 Write integration tests for service boundaries
    - `@pytest.mark.integration`: Xbox OAuth + retrieval → `GameRecord`; Tavily enrichment parsing; Gmail read-only restricted query + parsing; AgentCore Memory store/retrieve for records, platform list, and sessions
    - _Requirements: 3.2, 3.3, 5.1, 5.2, 6.3, 8.1_

  - [ ] 9.3 Write end-to-end conversational test
    - `@pytest.mark.e2e`: full mood → time → platform gate → recommendation → alternatives flow plus library assembly across sources and degradation, with services mocked at the network edge
    - _Requirements: 1.1, 3.1, 6.5, 7.1, 7.4, 10.2, 10.3, 10.4_

- [ ] 10. Final checkpoint
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core implementation tasks are never optional.
- Property tests are consolidated by module: each lists the design's property numbers (P1–P22) and the requirement clauses they validate.
- Requirement references use the 10-requirement numbering from `requirements.md`; property references use the design's P1–P22.
- The single `GameRecord` contract (task 2.2) is the only owned-game record type; it replaces the prior `GameMetadata`/`GameHistoryEntry`/`ExtractedGameRecord` types.
- Checkpoints (tasks 5, 7, 10) ensure incremental validation at the service/source, agent, and integration seams.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.2"] },
    { "id": 2, "tasks": ["2.3", "3.1", "3.3"] },
    { "id": 3, "tasks": ["2.4", "3.2", "3.4", "3.6"] },
    { "id": 4, "tasks": ["3.5", "3.7", "4.1", "6.2"] },
    { "id": 5, "tasks": ["4.2", "4.3", "4.4", "6.1"] },
    { "id": 6, "tasks": ["4.5", "4.6", "6.3", "6.4"] },
    { "id": 7, "tasks": ["6.5", "6.6"] },
    { "id": 8, "tasks": ["6.7", "6.8", "8.1"] },
    { "id": 9, "tasks": ["6.9", "8.2"] },
    { "id": 10, "tasks": ["8.3", "8.4", "8.5"] },
    { "id": 11, "tasks": ["8.6", "9.1"] },
    { "id": 12, "tasks": ["9.2", "9.3"] }
  ]
}
```
