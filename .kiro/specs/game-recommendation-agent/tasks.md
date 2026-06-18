# Implementation Plan: GameGusto (game-recommendation-agent)

## Overview

The backend and a runnable **headless conversation app** are complete, and the agent layer has been **re-architected into a tool-using Bedrock agent** (Task 12): project setup → locked data contract → core services (ErrorHandler, Bedrock via Converse, DynamoDB-backed memory, Tavily) → record sources (Gmail + manual) → agent layer (library assembly + tool registry + agent runtime) → application wiring (`bootstrap.build_app`) and a CLI (`cli.py`). The library is filled from read-only Gmail purchase emails and manual entry, persisted in DynamoDB, enriched via Tavily, and the agent — Claude Sonnet on Bedrock driven through the Converse tool-use loop — interprets the request, calls tools, and selects a recommendation with alternatives that honors the user's stated taste.

The original fixed mood→time→recommend pipeline (the `AgentOrchestrator` state machine, the deterministic `Recommender`, and the `MoodInterpreter`/`TimeParser`) has been **replaced** by `AgentRuntime` + `ToolRegistry`; the affected sub-tasks under Task 6 are marked **(superseded by Task 12)**.

The **remaining work is the Streamlit UI** (Task 9) and its application entry point (Task 10), followed by a final checkpoint (Task 11).

Every source produces and every consumer reads the single canonical `GameRecord` (`models/game_record.py`, data contract v2.0.0; provenance `gmail`/`manual`/`enrichment`).

## Tasks

> Tasks 1–8 (backend + headless app) and Task 12 (tool-using agent re-architecture) are complete. The only remaining work is the Streamlit UI (Task 9) and its entry point (Task 10), then the final checkpoint (Task 11).

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
  - [x] 3.2 Property test for error sanitization (P14)
    - _Requirements: 10.1_
  - [x] 3.3 Implement BedrockService (Bedrock Converse base model)
    - `services/bedrock_service.py`: `bedrock-runtime` `converse` against `BEDROCK_MODEL_ID` (Claude Sonnet); `invoke_conversational` (extended thinking) + `invoke_with_schema` — the latter replaced by `converse_tools` in Task 12; raises `BedrockServiceError` on failure (hard dependency, no fallback)
    - _Requirements: 1.2, 7.2, 10.2_
  - [x] 3.4 Implement MemoryService
    - `services/memory_service.py`: records store, `Platform_List` CRUD, sessions, `is_available` for stateless degradation, behind the `MemoryClient` protocol
    - _Requirements: 3.4, 4.2, 5.2, 6.1, 6.2, 6.3, 8.1, 8.2, 10.3_
  - [x] 3.5 Property tests for memory round-trips (P7–P9)
    - _Requirements: 5.2, 6.1, 6.2, 6.3, 8.1, 8.2_
  - [x] 3.6 Implement TavilyService
    - `services/tavily_service.py`: `enrich`/`autocomplete` (>= 3 chars), free-tier rate limiting, cache-first, graceful degradation
    - _Requirements: 3.3, 5.1, 5.2, 5.4, 5.5, 10.4_
  - [x] 3.7 Property tests for Tavily behavior (P3, P13)
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
  - [x] 4.4 Property tests for Gmail privacy and scoping (P4–P6)
    - _Requirements: 3.2, 4.1, 4.2, 4.3_
  - [x] 4.5 Unit tests for Gmail parsers and source skipping
    - _Requirements: 3.2, 3.5_

- [x] 5. Checkpoint - services and sources
  - Ensure all tests pass.

- [x] 6. Agent layer
  - [x] 6.1 Implement MoodInterpreter — **(superseded by Task 12; module removed)**
    - `agent/mood_interpreter.py`: maps free text to mood dimensions; raises on LLM failure; clarifies only when the model reports the mood uninterpretable
    - _Requirements: 1.1, 1.2, 1.3, 10.2_
  - [x] 6.2 Implement TimeParser — **(superseded by Task 12; module removed)**
    - _Requirements: 1.4, 1.5, 1.6_
  - [x] 6.3 Property tests for mood and time intake (P1–P4) — **(superseded by Task 12; removed)**
    - _Requirements: 1.2, 1.3, 1.5, 1.6_
  - [x] 6.4 Implement LibraryService
    - `agent/library_service.py`: precedence Gmail then manual, dedup, cache-first Tavily enrichment, persistence, skip unavailable sources
    - _Requirements: 3.1, 3.4, 3.5, 5.1, 5.2, 8.1_
  - [x] 6.5 Property tests for library assembly (P1, P2)
    - _Requirements: 2.3, 3.1, 3.4, 3.5_
  - [x] 6.6 Implement Recommender — **(superseded by Task 12; selection moved into the agent)**
    - `agent/recommender.py`: owned-platform filter, confirmed-availability gate, review ranking, time budget, no-repeat (last 5), review-summary reasoning + model narrative (raises on LLM failure), up to 3 alternatives
    - _Requirements: 5.3, 7.1, 7.2, 7.3, 7.4, 7.5, 8.3, 10.2_
  - [x] 6.7 Property tests for the recommender (P15–P20) — **(superseded by Task 12; retired)**
    - _Requirements: 5.3, 7.1, 7.2, 7.3, 7.4, 7.5, 8.3_
  - [x] 6.8 Implement AgentOrchestrator — **(superseded by Task 12; replaced by AgentRuntime)**
    - `agent/orchestrator.py`: mood then time then platform gate then recommendation + alternatives; `needs_platforms` on empty `Platform_List`; stateless mode when memory is down
    - _Requirements: 1.1, 1.4, 6.5, 7.1, 7.4, 10.3_
  - [x] 6.9 Property test for the platform gate (P14) — **(superseded by Task 12; retired)**
    - _Requirements: 6.5_

- [x] 7. Checkpoint - agent layer
  - Ensure all tests pass.

- [x] 8. Backend wiring, persistence, and headless app
  - [x] 8.1 DynamoDB-backed memory client
    - `services/dynamodb_memory_client.py`: single-table design (`USER#<id>` / `DOC#<key>` / `EVENT#sessions#`), float<->Decimal at the boundary, behind the `MemoryClient` protocol; `scripts/provision_dynamodb.py` to create the table
    - _Requirements: 8.1, 8.2_
  - [x] 8.2 Application wiring
    - `bootstrap.py` (`build_app(config)`): constructs Bedrock, Tavily, DynamoDB-backed MemoryService, sources (Gmail when configured plus manual) in precedence order, LibraryService, and the agent (ToolRegistry + AgentRuntime — updated in Task 12)
    - _Requirements: 3.1, 3.5, 10.2, 10.3, 10.4_
  - [x] 8.3 Headless conversation entrypoint
    - `cli.py`: manual add, Gmail import/refresh, platform management, and a free-text agent conversation (updated in Task 12 to drive `AgentRuntime.send`)
    - _Requirements: 1.1, 3.2, 3.3, 7.1, 7.4_
  - [x] 8.4 Backend tests
    - DynamoDB round-trips over a fake table; full conversation-flow test over the real agent graph with the network edge faked (replaced by the Task 12 agent-flow e2e)
    - _Requirements: 8.1, 8.2, 1.1, 7.1, 7.4_

- [ ] 9. Retro arcade Streamlit UI (Req 9)
  - [x] 9.1 Implement the retro arcade theme
    - `ui/theme.py`: retro arcade CSS (Press Start 2P, neon/CRT), responsive media query, idempotent injection
    - _Requirements: 9.1, 9.2_
  - [ ] 9.2 Implement UI bootstrap/accessors
    - `ui/bootstrap.py`: build and cache the service graph in session state by delegating to `bootstrap.build_app`; expose `get_runtime`, `get_memory_service`, `get_user_id`, `get_autocomplete`; graceful degradation across memory/Tavily/Gmail
    - _Requirements: 3.5, 10.2, 10.3, 10.4_
  - [ ] 9.3 Implement the chat view
    - `ui/chat_view.py`: conversational chat consuming the runtime's turn events (9.7). Render the model's inter-turn narration ("Let me check your library…") and tool calls as **transient status** (e.g. `st.status` with a per-tool label like "🔧 searching the web…") that collapses, and the final recommendation + reasoning persistently in a distinct retro card; stateless notice when memory is down. (The headless `cli.py` keeps the simple concatenated text.)
    - _Requirements: 9.3_
  - [ ] 9.4 Implement the library/dashboard view
    - `ui/library_view.py`: platform manager (add/edit/remove), add/edit game via manual entry + autocomplete writing to the shared store, `GameRecord`s grouped/filterable by platform, recommendation history
    - _Requirements: 3.3, 6.1, 9.4, 9.5_
  - [ ] 9.5 Implement the sidebar
    - `ui/sidebar.py`: connect Gmail + trigger import showing imported count (sanitized errors on failure), and the chat / library view switch
    - _Requirements: 4.1, 9.6, 10.5_
  - [ ] 9.6 Write UI smoke tests
    - `inject_retro_theme` output contains the pixel font and a responsive media query; the chat view renders the agent reply text inside the retro `rec-card`, shows tool/narration status transiently, and shows the stateless notice when memory is down
    - _Requirements: 9.1, 9.2, 9.3_
  - [ ] 9.7 Expose agent turn events from `AgentRuntime` (for the UI's smoother UX)
    - Add an event/streaming API alongside `send` (e.g. `stream(user_text)` yielding per-turn events — narration `text` deltas and `tool_call` names — and a final `answer`), so the chat view can show thinking/tool-use transiently and persist only the final answer. `send` stays as the simple concatenated-text path the CLI uses. Optionally back it with Bedrock `ConverseStream` for token streaming later.
    - _Requirements: 9.3, 11.2_

- [ ] 10. Wire the Streamlit application entry point
  - [ ] 10.1 Implement `ui/app.py`
    - Set page config, inject the theme, render the sidebar (view switch + Gmail connect), and route to the chat or library view, building on `ui/bootstrap.py` (Gmail source present only when configured) with graceful degradation
    - _Requirements: 3.1, 3.5, 9.6, 10.2, 10.3, 10.4, 10.5_

- [ ] 11. Final checkpoint
  - Ensure the full gate is green (ruff, mypy, fast tests) and the Streamlit app launches; confirm the chat and library views work end to end.

- [x] 12. Tool-using Bedrock agent re-architecture (Req 1, 7, 11)
  - [x] 12.1 Add `converse_tools` + `ToolLoop` types to BedrockService
    - `services/bedrock_service.py`: one Converse tool-use turn (no extended thinking — interleaved `reasoningContent` is unrepresentable in the pinned SDK); `ConverseResult`/`ToolUse`; drop the now-unused `invoke_with_schema`; verified live against `eu.anthropic.claude-sonnet-4-6`
    - _Requirements: 7.2, 10.2, 11.1_
  - [x] 12.2 Tool registry wrapping the services
    - `agent/tools.py`: `ToolRegistry` with specs + dispatch for platforms CRUD, library read/filter, manual add, `set_game_fields`, import_gmail, enrich_game, web_search, recent recommendations, save_recommendation; `services/tavily_service.py` gains `web_search`
    - _Requirements: 3.x, 5.x, 6.x, 8.x, 11.1, 11.2, 11.4_
  - [x] 12.3 Platform-family matching
    - `agent/platform_match.py`: family-aware `platforms_match`/`owned_intersects` (Xbox ~ Xbox Series X; Switch ~ Nintendo Switch), exact fallback for unknown names; used by `get_library`
    - _Requirements: 5.3, 7.1, 7.6_
  - [x] 12.4 AgentRuntime + system prompt
    - `agent/runtime.py`: owns the tool-use loop, system prompt, tool registry, and history; bounded tool rounds; `AgentReply` (stateless flag); replaces `AgentOrchestrator`; in-conversation feedback ("already played it"/"shorter") handled by history
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 7.1, 7.2, 7.3, 7.4, 10.2, 11.2, 11.5_
  - [x] 12.5 Rewire bootstrap + CLI; remove the phase machine
    - `bootstrap.build_app` builds `ToolRegistry` + `AgentRuntime`; `cli.py` drives `AgentRuntime.send`; `SessionData.mood` becomes free-text; remove `orchestrator.py`/`recommender.py`/`mood_interpreter.py`/`time_parser.py` and `SessionState`
    - _Requirements: 1.1, 7.1, 11.4_
  - [x] 12.6 Tests for the new agent
    - `test_bedrock_tool_use.py`, `test_tools.py`, `test_platform_match.py`, `test_runtime.py`, and a scripted multi-turn `@e2e` `test_agent_flow.py`; updated memory/dynamodb tests for the free-text mood; retired the recommender/mood/time/gate property suites
    - _Requirements: 1.x, 7.x, 11.x; Properties P1–P14_
  - [x] 12.7 Align `.kiro` docs and steering
    - Update requirements (Req 1, 7, new Req 11), design (architecture, components, properties P1–P14), and steering (drop Xbox/AgentCore; region `eu-north-1`)
    - _Requirements: 2.x_
  - [x] 12.8 LLM-assisted enrichment + platform refinements
    - `agent/enricher.py`: Tavily web search → Bedrock structured classification (genre, completion playtime, availability, review), cache-first + graceful degradation; strip the dead keyword machinery from `TavilyService` (now web_search + autocomplete only). Microsoft Store records labelled `Xbox Series X/S`; PSP given its own platform family in `platform_match`. Re-scraped the live `default` library (Metal Slug → "Run-and-gun shooter"). Owned platforms set to Nintendo Switch, Nintendo Switch 2, Xbox Series X/S, PC, PSP.
    - _Requirements: 5.1, 5.3, 5.5, 6.1, 6.4, 7.6, 10.3_
  - [x] 12.9 Discovery pivot: recommend games the user does NOT own
    - Product decision (user, live testing): GameGusto recommends **new** games to buy/play, not picks from the backlog. The owned library is used to infer taste and to **exclude** already-owned titles; recommendations must be playable on an owned platform and avoid recently-recommended ones. Updated the `AgentRuntime` system prompt and the spec (intro, Req 7, design overview/flow/tool notes).
    - _Requirements: 1.3, 7.1, 7.2, 7.4, 8.3_

## Notes

- The single `GameRecord` contract (v2.0.0) is the only owned-game record type; provenance values are `gmail`, `manual`, `enrichment`. The contract is unchanged by the re-architecture.
- The agent is a Claude Sonnet base model on Bedrock driven through the Converse **tool-use loop** (`eu.anthropic.claude-sonnet-4-6`, region `eu-north-1`). The LLM is a hard dependency: failures surface as errors and are never replaced by mock/deterministic content. Memory (DynamoDB) and Tavily degrade gracefully.
- The tool-use loop runs **without** extended thinking: interleaved `reasoningContent` blocks returned with tool use cannot be round-tripped by the pinned boto3 (`SDK_UNKNOWN_MEMBER`). Revisit if/when boto3 is upgraded (then interleaved thinking + tool use becomes an option).
- Game selection is the model's judgment, not a deterministic ranker; the behavior is exercised by the scripted multi-turn e2e and steered by the system prompt + tools.
- GameGusto is a **discovery** tool: it recommends games the user does NOT own (taste-matched, playable on an owned platform), using the owned library for taste + exclusion (Task 12.9).
- Live integration tests against real Bedrock / DynamoDB / Gmail are optional and require credentials; offline equivalents (fake DynamoDB table, faked network edge) cover these boundaries in the fast suite.
- The headless `cli.py` is the current runnable entrypoint; the Streamlit UI (Tasks 9–10) will reuse `bootstrap.build_app`.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["9.2", "9.7"] },
    { "id": 1, "tasks": ["9.3", "9.4", "9.5"] },
    { "id": 2, "tasks": ["9.6", "10.1"] },
    { "id": 3, "tasks": ["11"] }
  ]
}
```
