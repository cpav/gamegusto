# Design Document

## Overview

GameGusto is a Python application that recommends the next video game to play based on the user's mood, available time, taste, and the platforms they own. A **tool-using agent** — a Claude Sonnet base model on Amazon Bedrock driven through the Converse API tool-use loop — is the reasoning core: it interprets the user's request, decides which tools to call (manage platforms, read/update the library, import from sources, enrich, web search, recall recent picks, persist sessions), asks for missing information only when needed, and **selects the recommendation itself** so the result honors the user's stated taste and genre. It returns one strong recommendation with clear reasoning plus up to three alternatives, and handles follow-ups ("I already played it", "something shorter") as turns in the same conversation.

The library is assembled from interchangeable **record sources** — read-only Gmail purchase-confirmation emails (valuable for Nintendo, which has no history API) and manual UI entry — all normalized into a **single canonical `Game_Record`**. Every record is enriched via Tavily (genre, playtime, platform availability, community review) and persisted in a DynamoDB-backed store so recommendations favor well-regarded titles playable on owned hardware and improve across sessions.

The application is wired by a top-level `bootstrap.build_app(config)` that constructs the whole graph (Bedrock, Tavily, DynamoDB-backed memory, record sources, library assembly, the tool registry, and the agent runtime). A headless conversational CLI (`cli.py`) is the current runnable entrypoint; the Streamlit UI described later is the planned interface. The UI is designed as a retro arcade machine offering two views: a conversational **chat** view and a **library/dashboard** view for managing platforms and games.

### Design Goals

- **The model is the agent.** The LLM interprets the request, chooses tools, and selects the game. There is no fixed phase machine and no deterministic picker overriding the model's choice.
- **One data contract.** Every source produces, and every consumer reads, the same `Game_Record`. No per-source record types.
- **Sources and tools are interchangeable.** A common `RecordSource` protocol means adding or removing a source never changes the agent runtime or UI; tools wrap services behind a registry so adding or removing a tool never changes the loop.
- **Least privilege & privacy.** Gmail is read-only, restricted to known retailers, and never persists raw email content.
- **Graceful degradation where it makes sense.** The LLM is a hard dependency — the agent loop requires the Bedrock model, and its failure surfaces as an error rather than silent degradation. Memory (DynamoDB) and Tavily, by contrast, degrade gracefully: the session runs statelessly when memory is down, and enrichment/web-search tools return empty results when Tavily is unavailable, without breaking the rest of the app.
- **Tool use without extended thinking.** The tool-use loop runs with thinking disabled: interleaved `reasoningContent` blocks returned alongside tool use must be echoed back verbatim, which the pinned boto3 cannot round-trip (it surfaces them as `SDK_UNKNOWN_MEMBER`). Running the loop without thinking keeps the message history portable; the model still reasons strongly over the tools.

## Architecture

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│            Streamlit UI (Retro Arcade Theme)                   │
│   ┌──────────────┐                  ┌───────────────────────┐  │
│   │  Chat View   │                  │  Library / Dashboard  │  │
│   │  rec cards + │                  │  platforms · games    │  │
│   │  alternatives│                  │  (by platform) · hist │  │
│   └──────────────┘                  │  add/edit · connect   │  │
│                                     └───────────────────────┘  │
└───────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼───────────────────────────────┐
│                       Agent Runtime                              │
│   Converse tool-use loop · system prompt · tool registry ·       │
│   conversation history  (the model decides the flow + the pick)  │
└───────┬───────────────────────────────────────────┬─────────────┘
        │  converse_tools(messages, tools, system)   │  dispatch(name, input)
        ▼                                            ▼
┌────────────────┐                        ┌──────────────────────────┐
│ BedrockService │                        │       Tool Registry      │
│  Converse tool │                        │  thin functions + JSON   │
│  loop turn     │                        │  schemas wrapping the    │
│  (no thinking) │                        │  services below          │
└───────┬────────┘                        └──┬────────┬──────────┬───┘
        ▼                                     ▼        ▼          ▼
   Bedrock (Claude Sonnet)        ┌────────────────┐ ┌──────────┐ ┌──────────────┐
                                  │ LibraryService │ │ Tavily   │ │ MemoryService│
                                  │ sources→dedup  │ │ enrich · │ │  (DynamoDB)  │
                                  │ →enrich→persist│ │ web srch │ │ records ·    │
                                  └──┬─────────┬───┘ └────┬─────┘ │ platforms ·  │
                                     ▼         ▼          ▼       │ sessions     │
                            ┌──────────────────────┐ Tavily API  └──────┬───────┘
                            │  RecordSource (proto) │ (free tier)        ▼
                            │  Gmail · Manual       │               DynamoDB
                            └──────────────────────┘             (single table)
```

### Layered Structure

Dependencies point one direction only: `ui → agent → services → models`. Lower layers never import higher ones.

- **models** — the `Game_Record` contract and supporting dataclasses.
- **services** — external boundaries: `BedrockService`, `MemoryService` (backed by `DynamoDBMemoryClient`), `TavilyService`, and the record sources (`GmailSource`, `ManualSource`).
- **agent** — `LibraryService`, `platform_match` (family-aware matching), `ToolRegistry`, and `AgentRuntime`.
- **ui** — chat view, library/dashboard view, theme.

The whole graph is assembled by `bootstrap.build_app(config)`: it constructs the Bedrock service, Tavily service, the DynamoDB-backed `MemoryService`, the record sources in precedence order (Gmail then manual), `LibraryService`, the `ToolRegistry`, and the `AgentRuntime`. The headless `cli.py` is the current runnable entrypoint; the Streamlit UI is built on the same wiring and is deferred.

### System Flow (recommendation)

The flow is not a fixed sequence — the model decides what to do each turn. A
typical recommendation conversation looks like:

1. User sends a free-text request (mood, time, taste/genre — whatever they choose to say).
2. `AgentRuntime` appends the message to history and calls Converse with the tool specs.
3. The model calls tools as it sees fit — e.g. `get_owned_platforms` (and asks the user to add one if the list is empty), `get_library` (optionally filtered), and `enrich_game`/`web_search` to fill gaps — matching platforms at the family level.
4. The model **selects** one primary game that honors the request, plus up to three alternatives, and calls `get_recent_recommendations` to avoid recent repeats.
5. The model emits a final answer with reasoning (including a community-review summary when known) and calls `save_recommendation` to persist the session.
6. Follow-ups ("I already played it", "something shorter") continue the same conversation; the model excludes the prior pick and offers the next best without re-asking what it already knows.

The loop runs until the model emits a final answer (`stopReason == "end_turn"`),
bounded by a per-turn cap on tool-call rounds so it always terminates.

### Library Assembly Flow

`LibraryService.refresh()` runs sources in precedence order and produces a clean, enriched, persisted library:

1. Run sources in order **Gmail → manual**; each returns `list[Game_Record]` (Req 3.1).
2. Deduplicate the combined stream against existing records by **normalized dedup key** (title + platform); earlier sources win (Req 3.5).
3. Enrich any record missing metadata via Tavily, cache-first from memory (Req 5.1, 5.2).
4. Persist the deduplicated, enriched records to the DynamoDB-backed store (Req 3.5, 8.1).
5. A source that is unavailable or unconfigured is skipped; the remaining sources still run and manual entry is always available (Req 3.6, 10.4).

## Source Exploration & Data Contract

Requirement 2 calls for the `Game_Record` schema to be **derived from a documented exploration** of what each source actually exposes, not from assumptions. This is a short, deliberate spike that **precedes locking the contract**.

### Exploration Spike (Req 2.1, 2.4)

A discovery task probes each source and records its real fields in `docs/data-contract.md`:

- **Gmail purchase emails** — per-retailer (Nintendo eShop, Microsoft Store) confirmation structure: how title, platform, and purchase date appear in subject/body. (The Microsoft Store parser may yield platform `"Xbox"`.)
- **Tavily responses** — which enrichment fields are reliably available (genre, playtime, platform availability, review score/sentiment).

For every field a source exposes, the doc records the field and the decision to **include or exclude** it from the contract (Req 2.4).

### Output: the finalized `Game_Record` contract

The spike's deliverable is the locked, versioned contract in `docs/data-contract.md`, realized in code as `models/game_record.py`. **The contract is finalized by the exploration task**; all sources and consumers conform to it from that point on. The schema is defined in [Data Models](#data-models) below.

## Components and Interfaces

### Record Sources (`services/sources/`)

Every source implements one protocol and returns canonical records. This is what makes sources interchangeable (Req 3.1, 3.6).

```python
# services/sources/base.py
from typing import Protocol
from models.game_record import GameRecord

class RecordSource(Protocol):
    """An interchangeable origin of Game_Records (Req 3.1)."""

    name: str  # "gmail" | "manual"

    def is_available(self) -> bool:
        """True when configured/connected and reachable (Req 3.6)."""
        ...

    def fetch_records(self) -> list[GameRecord]:
        """Return records conforming to the Data_Contract. Never raises to the
        caller — on failure returns [] and reports unavailability (Req 10.4)."""
        ...
```

#### GmailSource (Req 3.3, 4)

```python
# services/sources/gmail_source.py
from typing import Callable
from models.game_record import GameRecord
from services.error_handler import ErrorHandler

# A parser turns one matched email payload into a GameRecord (or None).
EmailParser = Callable[[dict], GameRecord | None]

class GmailSource:
    """Read-only Gmail import of purchase-confirmation emails (source='gmail').

    Least privilege: requests ONLY gmail.readonly (Req 4.1). Searches ONLY known
    purchase-confirmation senders (Req 3.3, 4.3). Extracts title/platform/
    purchase_date and discards raw email content (Req 4.2)."""

    name = "gmail"

    # Read-only scope only — no broader permission is ever requested (Req 4.1).
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

    # Known purchase-confirmation senders. The Gmail query is built ONLY from this
    # registry, so unrelated mail is never matched (Req 3.3, 4.3). Extensible.
    KNOWN_SENDERS: dict[str, str] = {
        "nintendo": "no-reply@accounts.nintendo.com",
        "microsoft_store": "microsoft-noreply@microsoft.com",
    }

    def __init__(
        self,
        token_path: str,
        parser_registry: dict[str, EmailParser] | None = None,
    ):
        # The source only needs the cached read-only token; the client-secrets
        # file is used once by scripts/gmail_authorize.py to mint it.
        self._token_path = token_path
        self._parsers = parser_registry or self._default_parsers()
        self._service = None  # lazily-built googleapiclient resource

    def is_available(self) -> bool:
        # Lazily authenticate so the assembly's availability gate reflects real
        # connectability rather than whether a fetch has already run.
        if self._service is None:
            self.authenticate()
        return self._service is not None

    def authenticate(self) -> bool:
        """Run the read-only OAuth flow (gmail.readonly) and cache the token (Req 4.1)."""
        ...

    def fetch_records(self) -> list[GameRecord]:
        """Search only known senders, parse matches into GameRecords with
        source='gmail' and purchase_date, discard raw content (Req 3.3, 4.2, 4.3).
        On failure returns [] (the import degrades; other sources continue)."""
        try:
            records: list[GameRecord] = []
            for sender_id, sender_addr in self.KNOWN_SENDERS.items():
                parser = self._parsers.get(sender_id)
                if parser is None:
                    continue
                for raw in self._search(f"from:{sender_addr}"):  # restricted query
                    record = parser(raw)        # extract minimal fields
                    if record is not None:
                        records.append(record)
                    # raw is discarded here — never stored (Req 4.2)
            self._available = True
            return records
        except Exception as exc:                # auth/fetch failure (Req 10.4)
            self._available = False
            self._last_error = ErrorHandler.sanitize_error(exc, "gmail")
            return []

    def _search(self, query: str) -> list[dict]: ...
    @staticmethod
    def _default_parsers() -> dict[str, EmailParser]: ...
```

#### ManualSource (Req 3.4)

```python
# services/sources/manual_source.py
from models.game_record import GameRecord

class ManualSource:
    """UI-driven entries (source='manual'). Records are appended by the library
    view and surfaced here so manual entry is just another RecordSource."""

    name = "manual"

    def __init__(self, memory: "MemoryService", user_id: str):
        self._memory = memory
        self._user_id = user_id

    def is_available(self) -> bool:
        return True   # manual entry is always available (Req 3.6)

    def fetch_records(self) -> list[GameRecord]:
        """Return user-entered records (source='manual') staged in memory."""
        ...
```

### LibraryService (`agent/library_service.py`)

Orchestrates source assembly: precedence, dedup, enrichment, persistence (Req 3.1, 3.5, 3.6, 5.1).

```python
# agent/library_service.py
from models.game_record import GameRecord
from services.sources.base import RecordSource
from services.tavily_service import TavilyService
from services.memory_service import MemoryService

class LibraryService:
    def __init__(
        self,
        sources: list[RecordSource],      # in precedence order: Gmail, manual
        tavily: TavilyService,
        memory: MemoryService,
    ):
        self._sources = sources
        self._tavily = tavily
        self._memory = memory

    def refresh(self, user_id: str) -> list[GameRecord]:
        """Run available sources in precedence order, dedup, enrich, persist."""
        existing = self._memory.get_records(user_id)
        seen = {r.dedup_key for r in existing}
        merged = list(existing)

        for source in self._sources:                  # Gmail → manual
            if not source.is_available():
                continue                               # skip; others continue (Req 3.6)
            for record in source.fetch_records():
                if record.dedup_key in seen:           # earlier source wins (Req 3.5)
                    continue
                seen.add(record.dedup_key)
                merged.append(self._enrich(record))

        self._memory.store_records(user_id, merged)    # (Req 3.5, 8.1)
        return merged

    def _enrich(self, record: GameRecord) -> GameRecord:
        """Cache-first enrichment via Tavily (Req 5.1, 5.2)."""
        if record.is_enriched():
            return record
        return self._tavily.enrich(record)
```

### TavilyService (`services/tavily_service.py`)

Enriches any `Game_Record` and powers autocomplete; rate-limited to the free tier; cache-first (Req 5.1, 5.4).

```python
# services/tavily_service.py
import time
from dataclasses import dataclass
from models.game_record import GameRecord, CommunityReview

@dataclass
class RateLimitState:
    requests_this_minute: int = 0
    minute_start: float = 0.0

class TavilyService:
    FREE_TIER_RPM = 60

    def __init__(self, api_key: str):
        self._api_key = api_key
        self._rate = RateLimitState()
        self._available = True

    def enrich(self, record: GameRecord) -> GameRecord:
        """Populate genre, estimated_playtime, platform availability, and
        community_review for a record, regardless of its source (Req 5.1).
        Missing fields are left unset and the record marked incomplete (Req 5.5)."""
        if not self._check_rate_limit():
            return record  # degrade: leave as-is (Req 5.4, 10.3)
        try:
            data = self._search(f"{record.title} video game genre playtime platforms review")
            return self._apply(record, data)
        except TavilyAPIError:
            self._available = False
            return record

    def autocomplete(self, query: str) -> list[str]:
        """Suggestions for manual entry; only after >= 3 characters (Req 3.4)."""
        if len(query) < 3 or not self._check_rate_limit():
            return []
        return self._extract_titles(self._search(f"{query} video game"))

    def _check_rate_limit(self) -> bool:
        """Stay within free-tier RPM (Req 5.4)."""
        now = time.time()
        if now - self._rate.minute_start >= 60:
            self._rate = RateLimitState(requests_this_minute=0, minute_start=now)
        if self._rate.requests_this_minute >= self.FREE_TIER_RPM:
            return False
        self._rate.requests_this_minute += 1
        return True

    @property
    def is_available(self) -> bool:
        return self._available
```

### MemoryService (`services/memory_service.py`)

The single store for `Game_Records`, the `Platform_List`, and sessions (Req 6, 8, 10.2). `MemoryService` is unchanged from its original design: it depends only on a small `MemoryClient` protocol (`get_value`/`put_value` for keyed documents, `append_event`/`list_events` for the session log) and is injected with a concrete client at construction. The concrete client is now `DynamoDBMemoryClient` (see below).

```python
# services/memory_service.py
from models.game_record import GameRecord
from models.platform import OwnedPlatform
from models.session import SessionData
from models.recommendation import Recommendation

class MemoryService:
    def __init__(self, client):           # a MemoryClient (e.g. DynamoDBMemoryClient)
        self._client = client
        self._available = True

    # --- Game_Records (single store for ALL sources + UI) ---
    def get_records(self, user_id: str) -> list[GameRecord]: ...
    def store_records(self, user_id: str, records: list[GameRecord]) -> bool:
        """Persist records. Applies the dedup key defensively so duplicates are
        never stored, and persists only contract fields (Req 3.5, 4.2)."""
        ...
    def upsert_record(self, user_id: str, record: GameRecord) -> bool:
        """Add or edit a single record (used by the library view, Req 9.5)."""
        ...

    # --- Platform_List (Req 6.1–6.4) ---
    def get_platform_list(self, user_id: str) -> list[OwnedPlatform]: ...
    def add_platform(self, user_id: str, platform: OwnedPlatform) -> bool: ...
    def update_platform(self, user_id: str, platform_id: str, new_name: str) -> bool: ...
    def remove_platform(self, user_id: str, platform_id: str) -> bool: ...

    # --- sessions / personalization (Req 8) ---
    def store_session(self, user_id: str, session: SessionData) -> bool: ...
    def get_recent_recommendations(self, user_id: str, sessions: int = 5) -> list[Recommendation]: ...

    @property
    def is_available(self) -> bool:
        return self._available
```

### DynamoDBMemoryClient (`services/dynamodb_memory_client.py`)

The concrete `MemoryClient` behind `MemoryService`, backing all persistence with a single DynamoDB table. The manual-entry UI path, the Gmail import, and the agent all read and write the same store (Req 6, 8, 10.2).

Single-table design, keyed per user:

- `PK = USER#<user_id>`
- `SK = DOC#<key>` — keyed documents (e.g. the records library and the platform list), read/written via `get_value` / `put_value`.
- `SK = EVENT#sessions#<ts>#<id>` — the append-only session log, written via `append_event` and read newest-first via `list_events`. The timestamp + uuid suffix keeps events chronologically sortable and collision-free.

DynamoDB's document API rejects `float` and requires `Decimal`, so values are converted to `Decimal` on write and back to `int`/`float` on read **at this boundary only** — callers keep working with plain Python types.

```python
# services/dynamodb_memory_client.py
from typing import Any

class DynamoDBMemoryClient:
    """Stores GameGusto memory in one DynamoDB table (single-table design).

    Implements the MemoryClient protocol. A boto3 Table resource may be injected
    for testing; otherwise one is created lazily for table_name in region_name."""

    def __init__(self, table_name: str, region_name: str | None = None, table: Any | None = None):
        ...

    def get_value(self, user_id: str, key: str) -> dict[str, Any] | None:
        """Read the DOC#<key> document for the user, Decimal -> int/float."""
        ...

    def put_value(self, user_id: str, key: str, value: dict[str, Any]) -> None:
        """Write the DOC#<key> document, float -> Decimal."""
        ...

    def append_event(self, user_id: str, key: str, event: dict[str, Any]) -> None:
        """Append to the EVENT#<key>#<ts>#<id> log (sortable, newest-first)."""
        ...

    def list_events(self, user_id: str, key: str, limit: int) -> list[dict[str, Any]]:
        """Return up to `limit` most-recent events for the key, newest first."""
        ...
```

### BedrockService (`services/bedrock_service.py`)

The boundary to the Claude Sonnet **base model** on Amazon Bedrock via the Bedrock Runtime **Converse API**. It is built from `Config` (`bedrock_model_id` + `bedrock_reasoning_budget_tokens`) and exposes two entry points:

- `invoke_conversational(prompt, session_id)` — a single-shot free-text reply with extended thinking enabled (used by the live healthcheck `scripts/check_llm.py`).
- `converse_tools(messages, tools, system)` — **one turn** of a tool-use loop, run **without** extended thinking. The `AgentRuntime` owns the loop and calls this repeatedly. It returns a `ConverseResult` carrying the `stop_reason`, the concatenated answer `text`, any `tool_uses`, and the raw `assistant_content` blocks (kept verbatim for the next turn). Only well-formed `text`/`toolUse` blocks are retained, so any block the SDK cannot represent (`SDK_UNKNOWN_MEMBER`, e.g. interleaved reasoning) is never echoed back.

The LLM is a **hard dependency**: any transport failure or malformed response raises `BedrockServiceError` with a sanitized message — there is **no fallback** to mock or deterministic output.

```python
# services/bedrock_service.py
from dataclasses import dataclass, field
from typing import Any
from config import Config

class BedrockServiceError(RuntimeError):
    """Raised when a Bedrock invocation fails; message is already sanitized."""

@dataclass
class ToolUse:
    tool_use_id: str
    name: str
    input: dict[str, Any]

@dataclass
class ConverseResult:
    stop_reason: str                            # "tool_use" | "end_turn" | ...
    text: str                                   # concatenated answer text
    tool_uses: list[ToolUse] = field(default_factory=list)
    assistant_content: list[dict] = field(default_factory=list)  # raw blocks to echo back

class BedrockService:
    def __init__(self, config: Config, client: Any | None = None): ...

    def invoke_conversational(self, prompt: str, session_id: str) -> str:
        """Single-shot free-text reply (extended thinking on). Raises on failure."""
        ...

    def converse_tools(
        self, messages: list[dict], tools: list[dict], system: str
    ) -> ConverseResult:
        """One Converse turn with `tools` available (no thinking). The runtime
        executes any requested tools and calls again until stop_reason==end_turn.
        Raises BedrockServiceError (sanitized) on transport failure."""
        response = self._client.converse(
            modelId=self._model_id,
            system=[{"text": system}],
            messages=messages,
            toolConfig={"tools": tools},
            inferenceConfig={"maxTokens": self._reasoning_budget + ANSWER_TOKEN_HEADROOM},
        )
        return self._parse_tool_turn(response)
```

### Tool Registry (`agent/tools.py`)

Each tool is a thin function plus a Converse `toolSpec` (JSON schema) wrapping an existing service. The model decides which to call; the registry only declares the surface and dispatches. **Selection of the game is the model's job, not a tool** — it reads the library, applies the user's taste/mood/time/owned platforms, and may enrich or web-search to fill gaps. Tools never raise: expected failures return `{"ok": False, "error": ...}` and the underlying services already degrade gracefully.

| Tool | Input | Backed by |
|---|---|---|
| `get_owned_platforms` | – | `MemoryService.get_platform_list` |
| `add_platform` / `remove_platform` | name / id | `MemoryService` |
| `get_library` | optional `platform`, `genre`, `has_playtime` | `MemoryService.get_records` (+ family-aware filter) |
| `add_manual_game` | title, platform, optional playtime/genre | `MemoryService.upsert_record` |
| `set_game_fields` | title, optional playtime/genre | `MemoryService.upsert_record` (manual playtime fill) |
| `import_gmail` | – | `LibraryService.refresh` (returns imported delta) |
| `enrich_game` | title | `TavilyService.enrich` + persist |
| `web_search` | query | `TavilyService.web_search` |
| `get_recent_recommendations` | optional n | `MemoryService.get_recent_recommendations` |
| `save_recommendation` | game_title, reasoning, optional mood/time/alternatives | `MemoryService.store_session` |

```python
# agent/tools.py
class ToolRegistry:
    def __init__(self, memory: MemoryService, library: LibraryService,
                 tavily: TavilyService, user_id: str): ...

    def specs(self) -> list[dict]:
        """The Converse toolSpec list advertised to the model."""
        ...

    def dispatch(self, name: str, tool_input: dict) -> dict:
        """Execute a tool by name; an unknown name returns an error result
        rather than raising, so a hallucinated tool never crashes the loop."""
        ...
```

Platform filtering and matching are **family-aware** via `agent/platform_match.py`: `platforms_match("Xbox", "Xbox Series X") == True`; names outside the known families (Xbox, PlayStation, Nintendo, PC) fall back to exact case-insensitive comparison so the free-text Platform_List stays extensible (Req 6.4, 7.6).

### AgentRuntime (`agent/runtime.py`)

Owns the Converse tool-use loop, the system prompt, the tool registry, and the conversation history. There is no phase state machine: each user message is one `send(...)` that runs the loop until the model emits a final answer, bounded by a per-turn cap on tool rounds. The system prompt encodes the behavior the brief requires: honor the whole request (taste/genre), reason about mood/time natively, select the game itself, match platforms by family, treat enrichment playtime as completion-time (not a session budget), handle in-conversation follow-ups, and call `save_recommendation` after presenting a pick.

```python
# agent/runtime.py
from dataclasses import dataclass, field

@dataclass
class AgentReply:
    message: str
    is_stateless_mode: bool = False     # memory unavailable -> personalization limited (Req 10.2)
    tool_calls: list[str] = field(default_factory=list)

class AgentRuntime:
    def __init__(self, bedrock: BedrockService, tools: ToolRegistry,
                 memory: MemoryService, system_prompt: str = SYSTEM_PROMPT):
        self._messages: list[dict] = []
        ...

    def reset(self) -> None:
        """Clear history to start a fresh conversation."""
        self._messages = []

    def send(self, user_text: str) -> AgentReply:
        """Run the tool loop for one user message until a final answer.
        Raises BedrockServiceError (sanitized) — the LLM is a hard dependency."""
        self._messages.append({"role": "user", "content": [{"text": user_text}]})
        for _ in range(MAX_TOOL_ITERATIONS):
            result = self._bedrock.converse_tools(self._messages, self._tools.specs(), self._system)
            if result.assistant_content:
                self._messages.append({"role": "assistant", "content": result.assistant_content})
            if result.stop_reason != "tool_use" or not result.tool_uses:
                return self._reply(result.text.strip())          # final answer
            tool_results = [
                {"toolResult": {"toolUseId": u.tool_use_id,
                                "content": [{"json": self._tools.dispatch(u.name, u.input)}],
                                "status": "success"}}
                for u in result.tool_uses
            ]
            self._messages.append({"role": "user", "content": tool_results})
        return self._reply(ITERATION_LIMIT_MESSAGE)              # cap reached
```

### Streamlit UI (`ui/`)

Two views under a retro arcade theme (Req 9). A view switcher (chat ⇄ library) lives in the sidebar alongside connection controls.

```python
# ui/app.py
import streamlit as st
from ui.theme import inject_retro_theme
from ui.sidebar import render_sidebar
from ui.chat_view import render_chat_view
from ui.library_view import render_library_view

def main():
    st.set_page_config(page_title="GameGusto", layout="wide")
    inject_retro_theme()                  # retro arcade theme (Req 9.1)
    view = render_sidebar()               # connection controls + view switch (Req 9.6)
    if view == "library":
        render_library_view()             # platforms, games, history (Req 9.4, 9.5)
    else:
        render_chat_view()                # chat + rec cards (Req 9.3)
```

```python
# ui/theme.py — retro arcade machine theme (Req 9.1, 9.2)
import streamlit as st

RETRO_ARCADE_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap');
:root {
    --arcade-bg: #0d0221;
    --arcade-neon-pink: #ff2e97;
    --arcade-neon-cyan: #2de2e6;
    --arcade-neon-yellow: #f9f871;
}
html, body, [class*="css"], .stMarkdown, .stButton button {
    font-family: 'Press Start 2P', monospace !important;
}
.stApp {
    background:
        repeating-linear-gradient(0deg, rgba(0,0,0,0.15) 0, rgba(0,0,0,0.15) 1px, transparent 1px, transparent 3px),
        radial-gradient(circle at center, #1a0540 0%, var(--arcade-bg) 100%);
    color: var(--arcade-neon-cyan);
}
h1, h2, h3 {
    color: var(--arcade-neon-pink);
    text-shadow: 0 0 6px var(--arcade-neon-pink), 0 0 12px var(--arcade-neon-pink);
}
.rec-card {
    border: 3px solid var(--arcade-neon-cyan);
    border-radius: 6px;
    box-shadow: 0 0 10px var(--arcade-neon-cyan), inset 0 0 12px rgba(45,226,230,0.25);
    padding: 1rem; background: rgba(13,2,33,0.85);
}
.stButton button {
    background: var(--arcade-neon-pink); color: #0d0221;
    border: 2px solid var(--arcade-neon-yellow); box-shadow: 0 4px 0 #b3005f;
}
/* Responsive: preserve theme on small screens (Req 9.2) */
@media (max-width: 640px) {
    html, body, [class*="css"] { font-size: 10px !important; }
    h1 { font-size: 1.1rem !important; }
    .rec-card { padding: 0.6rem; }
}
</style>
"""

def inject_retro_theme() -> None:
    """Inject the retro arcade CSS once per session (idempotent)."""
    if not st.session_state.get("_theme_injected"):
        st.markdown(RETRO_ARCADE_CSS, unsafe_allow_html=True)
        st.session_state["_theme_injected"] = True
```

```python
# ui/chat_view.py — conversational chat (Req 9.3)
import streamlit as st

def render_chat_view():
    """Chat with the agent; the agent's reply (its recommendation + reasoning) is
    free-text rendered inside a retro 'rec-card'. The agent decides the flow, so
    the view just relays turns; it shows the stateless notice when memory is down."""
    runtime = get_runtime()
    for msg in st.session_state.setdefault("messages", []):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    if prompt := st.chat_input("Insert coin... type your message"):
        st.session_state["messages"].append({"role": "user", "content": prompt})
        reply = runtime.send(prompt)              # AgentReply (may raise on LLM failure)
        with st.chat_message("assistant"):
            st.markdown(f'<div class="rec-card">{reply.message}</div>', unsafe_allow_html=True)
            if reply.is_stateless_mode:
                st.caption("⚠️ memory unavailable — personalization is limited")
        st.session_state["messages"].append({"role": "assistant", "content": reply.message})
```

```python
# ui/library_view.py — library/dashboard view (Req 9.4, 9.5)
import streamlit as st
from models.game_record import GameRecord
from models.platform import OwnedPlatform

def render_library_view():
    """Platforms, Game_Records grouped/filterable by platform, rec history,
    plus add/edit game and manage platforms — all writing to the same store."""
    memory = get_memory_service()
    user_id = get_user_id()

    _render_platform_manager(memory, user_id)     # add/edit/remove (Req 6.1)
    _render_add_game(memory, user_id)             # manual entry + autocomplete (Req 3.4)
    _render_library(memory, user_id)              # grouped/filterable (Req 9.4)
    _render_history(memory, user_id)              # recommendation history (Req 9.4)

def _render_platform_manager(memory, user_id):
    """Add/edit/remove owned platforms; free-text so any platform works (Req 6.1, 6.4)."""
    st.subheader("🕹️ My Platforms")
    new = st.text_input("Add a platform", key="add_platform")
    if st.button("Add Platform") and new.strip():
        memory.add_platform(user_id, OwnedPlatform(name=new.strip()))
    for p in memory.get_platform_list(user_id):
        cols = st.columns([3, 1])
        edited = cols[0].text_input("", value=p.name, key=f"edit_{p.platform_id}")
        if cols[1].button("Remove", key=f"rm_{p.platform_id}"):
            memory.remove_platform(user_id, p.platform_id)
        elif edited != p.name:
            memory.update_platform(user_id, p.platform_id, edited)

def _render_add_game(memory, user_id):
    """Manual entry writes a source='manual' GameRecord to the shared store (Req 9.5)."""
    query = st.text_input("Add a game", key="add_game")
    if query and len(query) >= 3:                 # autocomplete threshold (Req 3.4)
        choice = st.selectbox("Suggestions", get_autocomplete(query))
        if st.button("Add Game") and choice:
            memory.upsert_record(user_id, GameRecord(title=choice, source="manual"))

def _render_library(memory, user_id):
    """Game_Records grouped and filterable by platform (Req 9.4)."""
    ...

def _render_history(memory, user_id):
    """Past recommendations (Req 9.4)."""
    ...
```

```python
# ui/sidebar.py — connection controls + view switch (Req 9.6)
import streamlit as st

def render_sidebar() -> str:
    """Connect Gmail + import (show count), switch views."""
    with st.sidebar:
        st.header("GameGusto")
        view = st.radio("View", ["chat", "library"], horizontal=True)
        _render_gmail_connect_and_import()        # show imported count (Req 9.6)
    return view

def _render_gmail_connect_and_import():
    """Connect Gmail (read-only) and import; report imported count (Req 9.6).
    Optional and degradable: failures show a sanitized message (Req 10.4)."""
    st.subheader("📧 Gmail Purchases")
    if not is_gmail_connected():
        if st.button("Connect Gmail"):            # gmail.readonly only (Req 4.1)
            connect_gmail()
        return
    if st.button("Import purchases"):
        with st.spinner("Reading purchase confirmations..."):
            count, error = import_gmail(get_user_id())
        if error:
            st.warning(error)                     # sanitized (Req 10.4)
        else:
            st.success(f"Imported {count} games")  # (Req 9.6)
```

## Data Models

### The Unified Contract: `Game_Record`

One canonical record, produced by every source and read by every consumer. This replaces the previous `GameMetadata` / `GameHistoryEntry` / `ExtractedGameRecord` proliferation. **Finalized by the exploration task (Req 2); contract version 2.0.0.**

```python
# models/game_record.py
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

# Data_Contract v2.0.0 — provenance values.
Source = Literal["gmail", "manual", "enrichment"]

@dataclass
class CommunityReview:
    """Aggregated community sentiment/score for a game (Req 5.1, 7.3)."""
    score: float            # normalized 0.0–10.0
    sentiment_summary: str  # short summary used in reasoning (Req 7.3)
    source_count: int       # number of aggregated sources

@dataclass
class GameRecord:
    """The single canonical owned-game record. Every Record_Source emits this,
    and every consumer reads it. Field set is fixed by the Data_Contract (Req 2.2)."""

    title: str
    platforms: list[str] = field(default_factory=list)  # platforms the user owns it on
    source: Source = "manual"                            # provenance (Req 2.2, 3.x)

    # Optional, source- or enrichment-populated.
    purchase_date: date | None = None                    # set for Gmail imports (Req 3.3)
    genre: str | None = None                             # enrichment (Req 5.1)
    estimated_playtime: int | None = None                # minutes; enrichment (Req 5.1)
    community_review: CommunityReview | None = None      # enrichment (Req 5.1, 7.2)
    platform_availability: list[str] = field(default_factory=list)  # enrichment (Req 5.3)
    external_ids: dict[str, str] = field(default_factory=dict)      # e.g. {"eshop": "..."}

    @property
    def dedup_key(self) -> str:
        """Normalized title + platform key for cross-source dedup (Req 2.3, 3.5).
        Casefolded and whitespace-stripped so the same game from different sources
        matches regardless of provenance."""
        plat = self.platforms[0] if self.platforms else ""
        return f"{self.title.strip().casefold()}|{plat.strip().casefold()}"

    def is_enriched(self) -> bool:
        """True when enrichment fields are populated (drives cache-first, Req 5.1)."""
        return self.genre is not None and bool(self.platform_availability)
```

> Note on `game_title` vs `title`: the recommendation surface (`Recommendation`) exposes `game_title` for display; the canonical record uses `title`. The `save_recommendation` tool maps one to the other when persisting a session.

### Supporting Models

```python
# models/platform.py
from dataclasses import dataclass, field
import uuid

@dataclass
class OwnedPlatform:
    """A user-declared platform. `name` is free-text so the list is extensible
    without code changes (Req 6.4)."""
    name: str
    platform_id: str = field(default_factory=lambda: str(uuid.uuid4()))
```

```python
# models/recommendation.py
from dataclasses import dataclass, field
from models.game_record import CommunityReview

@dataclass
class Recommendation:
    """Display-facing recommendation derived from a GameRecord."""
    game_title: str
    genre: str | None
    estimated_playtime: int | None             # minutes
    reasoning: str                             # detailed reasoning (primary)
    brief_reasoning: str = ""                  # short reasoning (alternatives)
    platform_availability: list[str] = field(default_factory=list)
    community_review: CommunityReview | None = None
```

```python
# models/session.py
from dataclasses import dataclass, field
from models.recommendation import Recommendation

@dataclass
class SessionData:
    """Completed session persisted to memory for personalization (Req 8.1).
    The agent drives the conversation itself (no fixed phase machine), so the
    in-memory SessionState of the old design is gone; the live conversation
    state is simply the AgentRuntime message history. `mood` is a free-text
    summary the agent supplies rather than fixed numeric dimensions."""
    user_id: str
    mood: str
    time_budget_minutes: int
    recommendation: Recommendation
    alternatives: list[Recommendation] = field(default_factory=list)
    user_feedback: str | None = None
```

### DynamoDB Memory Schema

A single DynamoDB table keyed by user (`PK = USER#<user_id>`), holding `Game_Records`, the `Platform_List`, and sessions. Keyed documents live under `SK = DOC#<key>`; the session log is appended under `SK = EVENT#sessions#<ts>#<id>`. The logical shapes are:

```python
# Per user: the canonical library (every source + manual UI write here)
{
    "user_id": "string",
    "records": [
        {
            "title": "Zelda: Tears of the Kingdom",
            "platforms": ["Nintendo Switch"],
            "source": "gmail",
            "purchase_date": "2023-05-12",
            "genre": "action-adventure",
            "estimated_playtime": 3000,
            "platform_availability": ["Nintendo Switch", "Nintendo Switch 2"],
            "community_review": {"score": 9.6, "sentiment_summary": "...", "source_count": 20},
            "external_ids": {}
        }
    ]
}

# Per user: owned platforms (Platform_List, Req 6.3)
{
    "user_id": "string",
    "platform_list": [
        {"platform_id": "uuid", "name": "Nintendo Switch 2"},
        {"platform_id": "uuid", "name": "Xbox Series S"}
    ]
}

# Per user session (Req 8.1)
{
    "user_id": "string",
    "session_id": "string",
    "timestamp": "ISO-8601",
    "mood": "relaxed, in the mood for a chill solo RPG",
    "time_budget_minutes": 90,
    "recommendation": { "game_title": "Hades", "...": "..." },
    "alternatives": [],
    "user_feedback": "loved it"
}
```

For Gmail-sourced records, only the contract fields (title, platform, purchase_date) plus Tavily-derived enrichment are persisted. Raw email content is never stored (Req 4.2).

## Error Handling

### Graceful Degradation

| Failure | Behavior | User message |
|---|---|---|
| Memory (DynamoDB) unavailable | Operate statelessly for the session; no persistence/personalization (Req 10.2) | "I'm running without memory right now — recommendations won't be personalized." |
| Bedrock LLM fails (hard dependency) | Mood interpretation / reasoning cannot proceed; raise `BedrockServiceError` (sanitized). No fallback (Req 10.1) | "The recommendation engine is temporarily unavailable. Please try again." |
| Tavily unavailable | Recommend from existing records + input + platforms; mark availability/ratings unverified (Req 10.3) | "I couldn't verify platform availability and ratings — recommending from what I know." |
| Tavily rate limit reached | Return empty enrichment/autocomplete rather than calling the API (Req 5.4) | Autocomplete silently stops; enrichment degrades as above. |
| Gmail source fails / not connected | Skip the import; continue on manual; manual always available (Req 3.6, 10.4) | "Couldn't read your Gmail purchases right now. The rest of the app still works." |
| Empty Platform_List | Block recommendation; prompt to add a platform (Req 6.5) | "Tell me which platforms you own before I recommend." |

### Error Sanitization

```python
# services/error_handler.py
class ErrorHandler:
    GENERIC_MESSAGES = {
        "memory_unavailable": "Personalization is temporarily limited. Recommendations still work.",
        "tavily_unavailable": "Game lookup is temporarily unavailable. Using available information.",
        "gmail_unavailable": "Couldn't read your Gmail purchases right now. The rest of the app still works.",
        "llm_unavailable": "The recommendation engine is temporarily unavailable. Please try again.",
        "unknown": "Something went wrong. Let's try again.",
    }

    @staticmethod
    def sanitize_error(error: Exception, service: str) -> str:
        """User-friendly message with no technical details (Req 10.1, 10.4)."""
        return ErrorHandler.GENERIC_MESSAGES.get(
            f"{service}_unavailable", ErrorHandler.GENERIC_MESSAGES["unknown"]
        )
```

## Project Structure

```
gamegusto/
├── docs/
│   └── data-contract.md        # exploration output + locked Game_Record contract (Req 2)
├── ui/                         # planned Streamlit UI (deferred)
│   ├── app.py                  # entry point; theme + view switch
│   ├── theme.py                # retro arcade theme / CSS
│   ├── chat_view.py            # chat + recommendation card (Req 9.3)
│   ├── library_view.py         # platforms, games, history, add/edit (Req 9.4, 9.5)
│   └── sidebar.py              # connect Gmail + import + view switch (Req 9.6)
├── agent/
│   ├── runtime.py              # AgentRuntime: Converse tool-use loop + system prompt
│   ├── tools.py                # ToolRegistry: tool specs + dispatch wrapping services
│   ├── platform_match.py       # family-aware platform matching (Xbox ~ Xbox Series X)
│   └── library_service.py      # source assembly: precedence, dedup, enrich, persist
├── services/
│   ├── bedrock_service.py      # Bedrock Converse: converse_tools (loop) + invoke_conversational
│   ├── memory_service.py       # Game_Records + Platform_List + sessions (MemoryClient)
│   ├── dynamodb_memory_client.py # DynamoDB single-table MemoryClient
│   ├── tavily_service.py       # enrichment + autocomplete, rate-limited
│   ├── error_handler.py        # sanitization
│   └── sources/
│       ├── base.py             # RecordSource protocol
│       ├── gmail_source.py     # source='gmail' (read-only, known senders)
│       └── manual_source.py    # source='manual'
├── models/
│   ├── game_record.py          # GameRecord + CommunityReview (THE contract)
│   ├── platform.py             # OwnedPlatform
│   ├── recommendation.py       # Recommendation (display)
│   └── session.py              # SessionState + SessionData
├── tests/                      # unit + property + integration + e2e
├── bootstrap.py                # build_app(config): wires the whole graph
├── cli.py                      # headless conversational entrypoint (current runnable app)
├── config.py                   # environment configuration
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

### Configuration / Environment Variables

All credentials load from environment variables via `config.py`; secrets are never hardcoded. Document each name (no values) in `.env.example`.

| Variable | Purpose | Required |
|---|---|---|
| `AWS_REGION` | AWS region for Bedrock + DynamoDB | Yes |
| `BEDROCK_MODEL_ID` | Bedrock base-model id or cross-Region inference-profile id (Claude Sonnet) for the Converse API | Yes |
| `BEDROCK_REASONING_BUDGET_TOKENS` | Extended-thinking token budget for Converse | Optional (defaults applied) |
| `TAVILY_API_KEY` | Tavily enrichment/autocomplete | Yes |
| `DYNAMODB_TABLE_NAME` | DynamoDB table backing the memory store | Yes |
| `GMAIL_CREDENTIALS_PATH` | OAuth client-secrets JSON (used once by `scripts/gmail_authorize.py`) | Optional (Gmail) |
| `GMAIL_TOKEN_PATH` | Cached read-only Gmail token path (what the runtime source needs) | Optional (Gmail) |

Gmail is **optional**: if its variables are unset, the Gmail source is not constructed and `LibraryService` simply skips it — every other source and feature keeps working (Req 3.6).

## Testing Strategy

Layered per the project's testing-strategy steering: fast unit/property tests everywhere, integration and e2e where boundaries and flows justify the cost. Property tests use Hypothesis with a minimum of 100 iterations.

### Unit Tests (example-based)
- **Bedrock tool turn:** `converse_tools` parses tool-use and final-answer responses, drops `SDK_UNKNOWN_MEMBER` blocks, sends `system`/`toolConfig` without thinking, and sanitizes transport/malformed errors.
- **Tool dispatch:** every tool round-trips against the real service graph (in-memory memory, fake Tavily) — platforms CRUD, library + family-aware filters, manual add, `set_game_fields`, enrich, web_search, recent recs, save; unknown tool returns an error result.
- **Agent loop:** final answer, multi-round tool dispatch, the iteration cap fallback, the stateless flag, and history reset (scripted Bedrock, no network).
- **Platform matching:** family resolution and `platforms_match`/`owned_intersects` for same-family, cross-family, and unknown-name cases.
- **Tavily degradation:** enrichment/web_search failure and rate-limit produce documented degradation.
- **Gmail parsing:** per-retailer parsers turn representative Nintendo eShop / Microsoft Store emails into correct `GameRecord`s.
- **Wiring/CLI:** `bootstrap.build_app` wires the runtime offline; CLI command handlers behave as documented.
- **UI smoke (deferred):** `inject_retro_theme` produces CSS containing the pixel font and a responsive media query.

### Property-Based Tests (Hypothesis)
Map directly to the Correctness Properties below. Examples: dedup correctness, source-unavailability resilience, autocomplete threshold, Gmail scope/privacy/known-sender, Game_Record/Platform_List/session round-trips, platform-family matching, tool-registry totality, rate-limit compliance, error sanitization.

### Integration Tests (`@pytest.mark.integration`)
- **Bedrock Converse tool use:** a live `converse_tools` turn returns a well-formed tool-use/end-turn response for the configured Sonnet model.
- **Tavily API:** enrichment returns parseable genre/playtime/availability/review.
- **Gmail API:** read-only OAuth + restricted query retrieves and parses representative purchase emails (needs a test mailbox).
- **DynamoDB memory:** store/retrieve cycle for records, platform list, and sessions (single-table keys; Decimal/float round-trip).

### End-to-End Tests (`@pytest.mark.e2e`)
- Full agent conversation with a scripted model over the real graph (network edge faked): a taste-rich request yields a matching owned title, and an "I already played it" follow-up offers the next best within the same conversation.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.* Each property is validated by an automated test (Hypothesis with a minimum of 100 iterations where a range of inputs applies) tagged **Feature: game-recommendation-agent, Property {n}**.

> **Note on the re-architecture.** Game selection moved from a deterministic `Recommender` to the model's judgment, so the former deterministic-ranking properties (review-driven ranking, hard time-budget cut-off, deterministic no-repeat, mood→dimension mapping, time-string parsing, and the empty-platform gate) are **retired** as machine-checked invariants — they were guarantees about code that no longer makes the choice. The recommendation behavior they targeted (honoring taste, fitting time, avoiding repeats, requiring an owned platform) is now exercised by the scripted multi-turn **e2e** test and steered by the system prompt and tools. The properties below are the invariants that remain genuinely deterministic.

### Property 1: Dedup is precedence-aware and key-normalized

*For any* set of Game_Records drawn from multiple sources with overlapping titles/platforms, the assembled library SHALL contain no two records with the same normalized dedup key (casefolded, whitespace-stripped title + platform), and for each colliding key the surviving record SHALL come from the higher-precedence source (Gmail > manual). Every unique key from the inputs SHALL be present.

**Validates: Requirements 2.3, 3.1, 3.5**

### Property 2: Source unavailability does not break assembly

*For any* subset of available record sources, `LibraryService.refresh` SHALL succeed using only the available sources without raising, and manual entry SHALL remain usable regardless of which other sources are available.

**Validates: Requirements 3.6, 10.4**

### Property 3: Autocomplete activation threshold

*For any* query string with fewer than 3 characters, TavilyService autocomplete SHALL return an empty list; for queries of 3 or more characters it MAY return suggestions.

**Validates: Requirements 3.4**

### Property 4: Gmail import restricts to known purchase-confirmation senders

*For any* mailbox containing an arbitrary mix of purchase-confirmation emails from known senders and unrelated mail, every Game_Record produced by GmailSource SHALL originate from a known purchase-confirmation sender, and no record SHALL be produced from unrelated mail.

**Validates: Requirements 3.3, 4.3**

### Property 5: Gmail import retains only contract fields

*For any* purchase email with arbitrary content, the data retained and stored from a Gmail import SHALL consist solely of Game_Record contract fields (such as title, platform, purchase_date) plus enrichment, and SHALL NOT include raw email content.

**Validates: Requirements 4.2**

### Property 6: Gmail import requests read-only scope only

*For any* construction or authentication of GmailSource, the requested OAuth scope set SHALL be exactly `{gmail.readonly}` and SHALL NOT include any broader scope.

**Validates: Requirements 4.1**

### Property 7: Game_Record store round-trip

*For any* set of Game_Records (including enrichment fields and any source, including manual UI writes) stored via MemoryService, retrieving records for the same user SHALL return records with matching title, platforms, source, purchase_date, and enrichment fields (genre, estimated_playtime, platform_availability, community_review).

**Validates: Requirements 5.2, 9.5**

### Property 8: Platform_List CRUD round-trip

*For any* sequence of add, edit, and remove operations applied to a user's Platform_List, retrieving the Platform_List SHALL return exactly the set implied by those operations, and arbitrary free-text platform names SHALL be supported without error.

**Validates: Requirements 6.1, 6.2, 6.3, 6.4**

### Property 9: Session persistence round-trip

*For any* completed session (recommendation, mood summary, time budget, alternatives, feedback) stored via MemoryService, retrieving recent recommendations for the same user SHALL return the stored primaries newest-first.

**Validates: Requirements 8.1, 8.2**

### Property 10: Platform matching is family-aware

*For any* owned platform name and available platform name that resolve to the same known family (Xbox, PlayStation, Nintendo, PC), `platforms_match` SHALL return true; for names in different known families it SHALL return false; for names outside the known families it SHALL match exactly (case-insensitive, whitespace-trimmed).

**Validates: Requirements 5.3, 7.1, 7.6**

### Property 11: Tool registry is total

*For any* tool advertised in the registry's specs there SHALL be a handler, and *for any* tool name (including unknown names) `dispatch` SHALL return a result dict without raising — an unknown name yielding an error result rather than an exception.

**Validates: Requirements 11.1, 11.2, 11.4**

### Property 12: The agent loop always terminates

*For any* sequence of model turns, `AgentRuntime.send` SHALL return an `AgentReply` within the bounded number of tool-call rounds — either the model's final answer or a clear fallback message — and SHALL NOT loop unboundedly.

**Validates: Requirements 11.5**

### Property 13: Tavily rate-limit compliance

*For any* sequence of Tavily calls (enrichment, autocomplete, or web search), TavilyService SHALL not exceed the free-tier limit (60 requests per minute); any call that would exceed the limit SHALL return empty results rather than calling the API.

**Validates: Requirements 5.4**

### Property 14: Error messages never expose technical details

*For any* exception raised by an external service (the Bedrock model, memory (DynamoDB), Tavily, or Gmail), the message shown to the user SHALL not contain stack traces, API keys, endpoint URLs, or internal error codes.

**Validates: Requirements 10.1, 10.4**
