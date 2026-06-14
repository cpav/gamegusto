# Design Document

## Overview

GameGusto is a Python application that recommends the next video game to play based on the user's mood, available time, taste, and the platforms they own. A conversational agent on AWS Bedrock AgentCore interprets mood and time, draws on a personal library, and returns one strong recommendation with clear reasoning plus optional alternatives.

The library is assembled from interchangeable **record sources** — the Xbox platform API, read-only Gmail purchase-confirmation emails (valuable for Nintendo, which has no history API), and manual UI entry — all normalized into a **single canonical `Game_Record`**. Every record is enriched via Tavily (genre, playtime, platform availability, community review) and persisted in AgentCore Memory so recommendations favor well-regarded titles playable on owned hardware and improve across sessions.

The UI is built with Streamlit, styled as a retro arcade machine, and offers two views: a conversational **chat** view and a **library/dashboard** view for managing platforms and games.

### Design Goals

- **One data contract.** Every source produces, and every consumer reads, the same `Game_Record`. No per-source record types.
- **Sources are interchangeable.** A common `RecordSource` protocol means adding or removing a source never changes the orchestrator, recommender, or UI.
- **Least privilege & privacy.** Gmail is read-only, restricted to known retailers, and never persists raw email content.
- **Graceful degradation.** Any single source, Tavily, or memory can fail without breaking the rest of the app.

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
│                    Agent Orchestrator                            │
│   mood → time → platform gate → recommendation → alternatives    │
└───────┬──────────────────────────────────────────┬──────────────┘
        │                                           │
        ▼                                           ▼
┌────────────────┐                        ┌──────────────────────┐
│ LibraryService │                        │     Recommender      │
│  run sources   │                        │  filter to owned     │
│  → dedup       │                        │  platforms · rank by │
│  → enrich      │                        │  review · time budget│
│  → persist     │                        │  · no-repeat         │
└──┬─────────┬───┘                        └──────────┬───────────┘
   │         │                                       │
   ▼         ▼                                       ▼
┌─────────────────────────┐   ┌──────────────┐  ┌──────────────────┐
│   RecordSource (proto)   │   │ TavilyService│  │  MemoryService   │
│  ┌────────┬───────┬────┐ │   │  enrich +    │  │ (AgentCore Mem)  │
│  │ Xbox   │ Gmail │Man-│ │   │  autocomplete│  │ Game_Records ·   │
│  │ Source │Source │ual │ │   │  rate-limited│  │ platforms ·      │
│  └────────┴───────┴────┘ │   │  cache-first │  │ sessions         │
└─────────────────────────┘   └──────────────┘  └──────────────────┘
        │          │                  │                  │
        ▼          ▼                  ▼                  ▼
   Xbox API   Gmail API          Tavily API        Bedrock AgentCore
              (readonly)         (free tier)
```

### Layered Structure

Dependencies point one direction only: `ui → agent → services → models`. Lower layers never import higher ones.

- **models** — the `Game_Record` contract and supporting dataclasses.
- **services** — external boundaries: `MemoryService`, `TavilyService`, and the record sources (`XboxSource`, `GmailSource`, `ManualSource`).
- **agent** — `LibraryService`, `Recommender`, `MoodInterpreter`, `TimeParser`, `AgentOrchestrator`.
- **ui** — chat view, library/dashboard view, theme.

### System Flow (recommendation)

1. User opens the app → session initializes; retro arcade theme is injected.
2. Agent asks about mood → interprets mood dimensions (clarifies if needed).
3. Agent asks about available time → parses to minutes (clarifies if ambiguous).
4. Orchestrator loads the `Game_Record` library and `Platform_List` from memory.
5. If `Platform_List` is empty, the agent prompts the user to add a platform before recommending (Req 6.5).
6. `Recommender` filters candidates to those whose availability intersects owned platforms, drops games recommended in the last 5 sessions, ranks the rest by community review within the time budget.
7. Primary recommendation renders in a retro card with reasoning that includes a community-review summary; alternatives in an expandable section.
8. Session data is persisted to AgentCore Memory.

### Library Assembly Flow

`LibraryService.refresh()` runs sources in precedence order and produces a clean, enriched, persisted library:

1. Run sources in order **Xbox → Gmail → manual**; each returns `list[Game_Record]` (Req 3.1).
2. Deduplicate the combined stream against existing records by **normalized dedup key** (title + platform); earlier sources win (Req 3.5).
3. Enrich any record missing metadata via Tavily, cache-first from memory (Req 5.1, 5.2).
4. Persist the deduplicated, enriched records to AgentCore Memory (Req 3.5, 8.1).
5. A source that is unavailable or unconfigured is skipped; the remaining sources still run and manual entry is always available (Req 3.6, 10.4).

## Source Exploration & Data Contract

Requirement 2 calls for the `Game_Record` schema to be **derived from a documented exploration** of what each source actually exposes, not from assumptions. This is a short, deliberate spike that **precedes locking the contract**.

### Exploration Spike (Req 2.1, 2.4)

A discovery task probes each source and records its real fields in `docs/data-contract.md`:

- **Xbox platform API** — title, platform/device, acquisition/play data, and any IDs returned per owned title.
- **Gmail purchase emails** — per-retailer (Nintendo eShop, Microsoft Store) confirmation structure: how title, platform, and purchase date appear in subject/body.
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

    name: str  # "xbox" | "gmail" | "manual"

    def is_available(self) -> bool:
        """True when configured/connected and reachable (Req 3.6)."""
        ...

    def fetch_records(self) -> list[GameRecord]:
        """Return records conforming to the Data_Contract. Never raises to the
        caller — on failure returns [] and reports unavailability (Req 10.4)."""
        ...
```

#### XboxSource (Req 3.2)

```python
# services/sources/xbox_source.py
from models.game_record import GameRecord

class XboxSource:
    """Owned games from the Xbox platform API (source='xbox')."""

    name = "xbox"

    def __init__(self, client_id: str, client_secret: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None

    def is_available(self) -> bool:
        return self._token is not None

    def authenticate(self, auth_code: str) -> bool:
        """OAuth2 authorization-code flow for the platform API."""
        ...

    def fetch_records(self) -> list[GameRecord]:
        """Map every retrieved title to a GameRecord, populating all available
        metadata with source='xbox' (Req 3.2)."""
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
        "microsoft_store": "account-security-noreply@accountprotection.microsoft.com",
    }

    def __init__(
        self,
        credentials_path: str,
        token_path: str,
        redirect_uri: str,
        parser_registry: dict[str, EmailParser] | None = None,
    ):
        self._credentials_path = credentials_path
        self._token_path = token_path
        self._redirect_uri = redirect_uri
        self._parsers = parser_registry or self._default_parsers()
        self._service = None  # lazily-built googleapiclient resource
        self._available = False

    def is_available(self) -> bool:
        return self._available

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

    def __init__(self, memory: "MemoryService"):
        self._memory = memory

    def is_available(self) -> bool:
        return True   # manual entry is always available (Req 3.6)

    def fetch_records(self) -> list[GameRecord]:
        """Return user-entered records staged in memory."""
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
        sources: list[RecordSource],      # in precedence order: Xbox, Gmail, manual
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

        for source in self._sources:                  # Xbox → Gmail → manual
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

The single store for `Game_Records`, the `Platform_List`, and sessions in AgentCore Memory (Req 6, 8, 10.2).

```python
# services/memory_service.py
from models.game_record import GameRecord
from models.platform import OwnedPlatform
from models.session import SessionData
from models.recommendation import Recommendation

class MemoryService:
    def __init__(self, agentcore_client):
        self._client = agentcore_client
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

### MoodInterpreter & TimeParser (`agent/`)

```python
# agent/mood_interpreter.py
from dataclasses import dataclass
from services.bedrock_service import BedrockService

@dataclass
class MoodDimensions:
    energy_level: float        # 0.0–1.0
    stress_level: float        # 0.0–1.0
    social_desire: float       # 0.0–1.0
    challenge_appetite: float  # 0.0–1.0

@dataclass
class MoodInterpretation:
    mood_dimensions: MoodDimensions | None
    needs_clarification: bool
    clarification_question: str | None

class MoodInterpreter:
    def __init__(self, bedrock: BedrockService):
        self._bedrock = bedrock

    def interpret(self, text: str) -> MoodInterpretation:
        """Map free-text mood to dimensions; flag clarification when unclear
        (Req 1.2, 1.3)."""
        ...
```

```python
# agent/time_parser.py
from dataclasses import dataclass
import re

@dataclass
class TimeParseResult:
    minutes: int | None
    needs_clarification: bool
    clarification_question: str | None

class TimeParser:
    PATTERNS = [
        (r"(\d+(?:\.\d+)?)\s*h\w*\s*(?:and\s*)?(\d+)\s*m",
         lambda m: int(float(m.group(1)) * 60) + int(m.group(2))),
        (r"(\d+)\s*h\w*", lambda m: int(m.group(1)) * 60),
        (r"(\d+)\s*m\w*", lambda m: int(m.group(1))),
    ]
    AMBIGUOUS = ["a bit", "a little", "some time", "a while", "not long"]

    def parse(self, text: str) -> TimeParseResult:
        """Parse explicit hours/minutes to a positive int; clarify on vague
        input (Req 1.5, 1.6)."""
        t = text.lower().strip()
        if any(p in t for p in self.AMBIGUOUS):
            return TimeParseResult(None, True,
                "Could you give a rough estimate, like '30 minutes' or '2 hours'?")
        for pattern, extract in self.PATTERNS:
            if (m := re.search(pattern, t)):
                return TimeParseResult(extract(m), False, None)
        return TimeParseResult(None, True,
            "How much time do you have? Try '45 minutes' or '1 hour'.")
```

### Recommender (`agent/recommender.py`)

Operates entirely over `Game_Records`: filter to owned platforms, rank by review, respect time budget, avoid repeats, build reasoning with a review summary (Req 5.3, 7).

```python
# agent/recommender.py
from models.game_record import GameRecord
from models.platform import OwnedPlatform
from models.recommendation import Recommendation
from agent.mood_interpreter import MoodDimensions
from services.bedrock_service import BedrockService
from services.memory_service import MemoryService

class Recommender:
    def __init__(self, bedrock: BedrockService, memory: MemoryService):
        self._bedrock = bedrock
        self._memory = memory

    def recommend(
        self,
        mood: MoodDimensions,
        time_budget_minutes: int,
        library: list[GameRecord],
        owned_platforms: list[OwnedPlatform],
        user_id: str,
    ) -> Recommendation:
        """One primary recommendation, playable + within budget + well-reviewed."""
        recent = {r.game_title for r in self._memory.get_recent_recommendations(user_id, 5)}
        owned = {p.name.casefold() for p in owned_platforms}

        eligible = [
            g for g in library
            if g.game_title not in recent                      # no-repeat (Req 8.3)
            and self._is_playable(g, owned)                    # owned platform (Req 5.3, 7.1)
            and g.estimated_playtime is not None
            and g.estimated_playtime <= time_budget_minutes    # time budget (Req 7.1)
        ]
        eligible.sort(key=self._review_score, reverse=True)    # rank by review (Req 7.2)
        primary = self._select_primary(eligible, mood)
        primary.reasoning = self._build_reasoning(primary, mood, time_budget_minutes, owned_platforms)
        return primary

    def alternatives(self, context, owned_platforms, max_count: int = 3) -> list[Recommendation]:
        """Up to 3 alternatives, each playable on an owned platform (Req 7.4)."""
        ...

    @staticmethod
    def _is_playable(g: GameRecord, owned: set[str]) -> bool:
        """Confirmed availability intersecting owned platforms. Unconfirmed
        availability is excluded from the primary (Req 5.3, 7.5)."""
        if not g.platform_availability:           # unconfirmed → excluded
            return False
        return any(p.casefold() in owned for p in g.platform_availability)

    @staticmethod
    def _review_score(g: GameRecord) -> float:
        """Rank key; missing review sorts below any reviewed record (Req 7.2)."""
        return g.community_review.score if g.community_review else -1.0

    def _build_reasoning(self, g, mood, minutes, owned) -> str:
        """Reasoning includes the community-review summary, or notes it is
        unavailable (Req 7.3, 7.5)."""
        ...
```

### AgentOrchestrator (`agent/orchestrator.py`)

Drives the conversation: mood → time → platform gate → recommendation → alternatives (Req 1, 6.5, 7).

```python
# agent/orchestrator.py
from dataclasses import dataclass
from agent.mood_interpreter import MoodInterpreter
from agent.time_parser import TimeParser
from agent.recommender import Recommender
from agent.library_service import LibraryService
from services.memory_service import MemoryService
from models.session import SessionState
from models.recommendation import Recommendation

@dataclass
class AgentResponse:
    message: str
    recommendation: Recommendation | None = None
    alternatives: list[Recommendation] | None = None
    error: str | None = None
    is_stateless_mode: bool = False
    needs_platforms: bool = False   # set when empty Platform_List blocks rec (Req 6.5)

class AgentOrchestrator:
    def __init__(
        self,
        mood_interpreter: MoodInterpreter,
        time_parser: TimeParser,
        recommender: Recommender,
        library_service: LibraryService,
        memory_service: MemoryService,
    ):
        self._mood = mood_interpreter
        self._time = time_parser
        self._recommender = recommender
        self._library = library_service
        self._memory = memory_service
        self.session = SessionState()

    def process_message(self, user_input: str) -> AgentResponse:
        """Route input by conversation phase."""
        phase = self.session.current_phase
        if phase == "mood_gathering":
            return self._handle_mood(user_input)
        if phase == "time_gathering":
            return self._handle_time(user_input)
        return self._generate_recommendation()

    def _generate_recommendation(self) -> AgentResponse:
        """Gate on Platform_List, then recommend (Req 6.5, 7.1)."""
        user_id = self.session.user_id
        platforms = self._memory.get_platform_list(user_id)
        if not platforms:
            self.session.current_phase = "platform_setup"
            return AgentResponse(
                message="Tell me which platforms you own before I recommend — "
                        "add at least one in the Library view.",
                needs_platforms=True,
            )
        library = self._library.refresh(user_id)
        rec = self._recommender.recommend(
            mood=self.session.mood,
            time_budget_minutes=self.session.time_budget_minutes,
            library=library,
            owned_platforms=platforms,
            user_id=user_id,
        )
        self.session.primary_recommendation = rec
        return AgentResponse(message=rec.reasoning, recommendation=rec)
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
# ui/chat_view.py — conversational chat + recommendation card (Req 9.3)
import streamlit as st
from models.recommendation import Recommendation

def render_chat_view():
    """Chat with the agent; primary rec in a card, alternatives expandable."""
    orchestrator = get_orchestrator()
    for msg in st.session_state.get("messages", []):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    if prompt := st.chat_input("Insert coin... type your message"):
        response = orchestrator.process_message(prompt)
        if response.recommendation:
            _render_card(response.recommendation)

def _render_card(rec: Recommendation):
    st.markdown('<div class="rec-card">', unsafe_allow_html=True)
    st.subheader(f"🎮 {rec.game_title}")
    st.markdown(f"**Why this game:** {rec.reasoning}")
    review = rec.community_review
    review_line = (f"⭐ {review.score:.1f}/10 — {review.sentiment_summary}"
                   if review else "⭐ community rating unavailable")
    st.caption(f"⏱️ ~{rec.estimated_playtime} min | 🎭 {rec.genre} | "
               f"🕹️ {', '.join(rec.platform_availability)}")
    st.caption(review_line)
    st.markdown('</div>', unsafe_allow_html=True)
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
    """Connect Xbox, connect Gmail + import (show count), switch views."""
    with st.sidebar:
        st.header("GameGusto")
        view = st.radio("View", ["chat", "library"], horizontal=True)
        _render_xbox_connect()
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

One canonical record, produced by every source and read by every consumer. This replaces the previous `GameMetadata` / `GameHistoryEntry` / `ExtractedGameRecord` proliferation. **Finalized by the exploration task (Req 2).**

```python
# models/game_record.py
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

Source = Literal["xbox", "gmail", "manual", "enrichment"]

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
    external_ids: dict[str, str] = field(default_factory=dict)      # e.g. {"xbox": "..."}

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

> Note on `game_title` vs `title`: the recommendation surface (`Recommendation`) exposes `game_title` for display; the canonical record uses `title`. The recommender maps one to the other when constructing a `Recommendation`.

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
from agent.mood_interpreter import MoodDimensions
from models.platform import OwnedPlatform
from models.recommendation import Recommendation

@dataclass
class SessionState:
    user_id: str = "anonymous"
    # mood_gathering | time_gathering | platform_setup | recommendation | alternatives
    current_phase: str = "mood_gathering"
    mood: MoodDimensions | None = None
    time_budget_minutes: int | None = None
    primary_recommendation: Recommendation | None = None
    alternatives: list[Recommendation] = field(default_factory=list)

@dataclass
class SessionData:
    user_id: str
    mood: MoodDimensions
    time_budget_minutes: int
    recommendation: Recommendation
    alternatives: list[Recommendation] = field(default_factory=list)
    user_feedback: str | None = None
```

### AgentCore Memory Schema

A single store keyed by user, holding `Game_Records`, the `Platform_List`, and sessions.

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
    "mood_dimensions": {"energy_level": 0.7, "stress_level": 0.3,
                         "social_desire": 0.5, "challenge_appetite": 0.8},
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
| AgentCore Memory unavailable | Operate statelessly for the session; no persistence/personalization (Req 10.2) | "I'm running without memory right now — recommendations won't be personalized." |
| Tavily unavailable | Recommend from existing records + input + platforms; mark availability/ratings unverified (Req 10.3) | "I couldn't verify platform availability and ratings — recommending from what I know." |
| Tavily rate limit reached | Return empty enrichment/autocomplete rather than calling the API (Req 5.4) | Autocomplete silently stops; enrichment degrades as above. |
| Xbox source fails | Skip Xbox; continue on Gmail + manual (Req 3.6, 10.4) | "Couldn't connect to Xbox. Your other games are unaffected." |
| Gmail source fails / not connected | Skip the import; continue on Xbox + manual; manual always available (Req 3.6, 10.4) | "Couldn't read your Gmail purchases right now. The rest of the app still works." |
| Empty Platform_List | Block recommendation; prompt to add a platform (Req 6.5) | "Tell me which platforms you own before I recommend." |

### Error Sanitization

```python
# services/error_handler.py
class ErrorHandler:
    GENERIC_MESSAGES = {
        "memory_unavailable": "Personalization is temporarily limited. Recommendations still work.",
        "tavily_unavailable": "Game lookup is temporarily unavailable. Using available information.",
        "xbox_unavailable": "Couldn't connect to Xbox. Your other games are unaffected.",
        "gmail_unavailable": "Couldn't read your Gmail purchases right now. The rest of the app still works.",
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
├── ui/
│   ├── app.py                  # entry point; theme + view switch
│   ├── theme.py                # retro arcade theme / CSS
│   ├── chat_view.py            # chat + recommendation card (Req 9.3)
│   ├── library_view.py         # platforms, games, history, add/edit (Req 9.4, 9.5)
│   └── sidebar.py              # connect Xbox/Gmail + import + view switch (Req 9.6)
├── agent/
│   ├── orchestrator.py         # conversation flow + platform gating
│   ├── library_service.py      # source assembly: precedence, dedup, enrich, persist
│   ├── recommender.py          # filter, rank, time budget, no-repeat, reasoning
│   ├── mood_interpreter.py     # mood → dimensions
│   └── time_parser.py          # time → minutes
├── services/
│   ├── bedrock_service.py      # AgentCore LLM client
│   ├── memory_service.py       # Game_Records + Platform_List + sessions
│   ├── tavily_service.py       # enrichment + autocomplete, rate-limited
│   ├── error_handler.py        # sanitization
│   └── sources/
│       ├── base.py             # RecordSource protocol
│       ├── xbox_source.py      # source='xbox'
│       ├── gmail_source.py     # source='gmail' (read-only, known senders)
│       └── manual_source.py    # source='manual'
├── models/
│   ├── game_record.py          # GameRecord + CommunityReview (THE contract)
│   ├── platform.py             # OwnedPlatform
│   ├── recommendation.py       # Recommendation (display)
│   └── session.py              # SessionState + SessionData
├── tests/                      # unit + property + integration + e2e
├── config.py                   # environment configuration
├── requirements.txt
├── requirements-dev.txt
└── README.md
```

### Configuration / Environment Variables

All credentials load from environment variables via `config.py`; secrets are never hardcoded. Document each name (no values) in `.env.example`.

| Variable | Purpose | Required |
|---|---|---|
| `AWS_REGION` | AWS region for Bedrock AgentCore | Yes |
| `BEDROCK_AGENT_ID` / `BEDROCK_AGENT_ALIAS_ID` | AgentCore agent + memory binding | Yes |
| `TAVILY_API_KEY` | Tavily enrichment/autocomplete | Yes |
| `XBOX_CLIENT_ID` / `XBOX_CLIENT_SECRET` | Xbox source OAuth client | Optional (Xbox) |
| `GMAIL_CREDENTIALS_PATH` | Gmail OAuth client-secrets JSON | Optional (Gmail) |
| `GMAIL_TOKEN_PATH` | Cached read-only Gmail token path | Optional (Gmail) |
| `GMAIL_REDIRECT_URI` | OAuth redirect URI for Gmail consent | Optional (Gmail) |

Xbox and Gmail are **optional**: if their variables are unset, the corresponding source is not constructed and `LibraryService` simply skips it — every other source and feature keeps working (Req 3.6).

## Testing Strategy

Layered per the project's testing-strategy steering: fast unit/property tests everywhere, integration and e2e where boundaries and flows justify the cost. Property tests use Hypothesis with a minimum of 100 iterations.

### Unit Tests (example-based)
- **Conversation flow:** orchestrator transitions mood → time → platform gate → recommendation.
- **Empty-platform gating:** empty `Platform_List` returns `needs_platforms=True` and no recommendation.
- **Time/mood clarification:** vague time and uninterpretable mood return clarification prompts.
- **Tavily degradation:** enrichment failure and rate-limit produce documented degradation/messaging.
- **Gmail parsing:** per-retailer parsers turn representative Nintendo eShop / Microsoft Store emails into correct `GameRecord`s.
- **UI smoke:** `inject_retro_theme` produces CSS containing the pixel font and a responsive media query; recommendation card includes title, reasoning, playtime, genre, availability, review line.
- **Source skip:** an unavailable source is skipped and the rest of the library still assembles.

### Property-Based Tests (Hypothesis)
Map directly to the Correctness Properties below (P1–P21). Examples: dedup correctness, every recommendation playable on an owned platform, review-driven ranking monotonicity, time-budget constraint, no-repeat, rate-limit compliance, Game_Record/Platform_List round-trips, Gmail scope/privacy/known-sender, error sanitization.

### Integration Tests (`@pytest.mark.integration`)
- **Xbox API:** OAuth + owned-title retrieval mapped to `GameRecord`.
- **Tavily API:** enrichment returns parseable genre/playtime/availability/review.
- **Gmail API:** read-only OAuth + restricted query retrieves and parses representative purchase emails (needs a test mailbox).
- **AgentCore Memory:** store/retrieve cycle for records, platform list, and sessions.

### End-to-End Tests (`@pytest.mark.e2e`)
- Full mood → time → platform gate → recommendation → alternatives flow, with services mocked at the network edge.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.* Each property is validated by a Hypothesis test (minimum 100 iterations) tagged **Feature: game-recommendation-agent, Property {n}**.

### Property 1: Mood interpretation produces valid dimensions

*For any* non-empty, meaningful free-text mood input, when the MoodInterpreter returns mood dimensions, every dimension (energy_level, stress_level, social_desire, challenge_appetite) SHALL be a float in [0.0, 1.0].

**Validates: Requirements 1.2**

### Property 2: Uninterpretable mood triggers clarification

*For any* mood input that is empty, gibberish, or unrelated to emotional state, the MoodInterpreter SHALL return `needs_clarification=True` with a non-empty `clarification_question`.

**Validates: Requirements 1.3**

### Property 3: Time budget parsing

*For any* time expression containing explicit numeric hours and/or minutes (e.g., "2 hours", "45 min", "1h30m"), the TimeParser SHALL parse it to the correct number of minutes as a positive integer.

**Validates: Requirements 1.5**

### Property 4: Ambiguous time triggers clarification

*For any* time input containing only vague phrases without numeric values (e.g., "a bit", "a while"), the TimeParser SHALL return `needs_clarification=True` with a non-empty `clarification_question`.

**Validates: Requirements 1.6**

### Property 5: Dedup is precedence-aware and key-normalized

*For any* set of Game_Records drawn from multiple sources with overlapping titles/platforms, the assembled library SHALL contain no two records with the same normalized dedup key (casefolded, whitespace-stripped title + platform), and for each colliding key the surviving record SHALL come from the higher-precedence source (Xbox > Gmail > manual). Every unique key from the inputs SHALL be present.

**Validates: Requirements 2.3, 3.1, 3.5**

### Property 6: Source unavailability does not break assembly

*For any* subset of available record sources, `LibraryService.refresh` SHALL succeed using only the available sources without raising, and manual entry SHALL remain usable regardless of which other sources are available.

**Validates: Requirements 3.6, 10.4**

### Property 7: Autocomplete activation threshold

*For any* query string with fewer than 3 characters, TavilyService autocomplete SHALL return an empty list; for queries of 3 or more characters it MAY return suggestions.

**Validates: Requirements 3.4**

### Property 8: Gmail import restricts to known purchase-confirmation senders

*For any* mailbox containing an arbitrary mix of purchase-confirmation emails from known senders and unrelated mail, every Game_Record produced by GmailSource SHALL originate from a known purchase-confirmation sender, and no record SHALL be produced from unrelated mail.

**Validates: Requirements 3.3, 4.3**

### Property 9: Gmail import retains only contract fields

*For any* purchase email with arbitrary content, the data retained and stored from a Gmail import SHALL consist solely of Game_Record contract fields (such as title, platform, purchase_date) plus enrichment, and SHALL NOT include raw email content.

**Validates: Requirements 4.2**

### Property 10: Gmail import requests read-only scope only

*For any* construction or authentication of GmailSource, the requested OAuth scope set SHALL be exactly `{gmail.readonly}` and SHALL NOT include any broader scope.

**Validates: Requirements 4.1**

### Property 11: Game_Record store round-trip

*For any* set of Game_Records (including enrichment fields and any source, including manual UI writes) stored via MemoryService, retrieving records for the same user SHALL return records with matching title, platforms, source, purchase_date, and enrichment fields (genre, estimated_playtime, platform_availability, community_review).

**Validates: Requirements 5.2, 9.5**

### Property 12: Platform_List CRUD round-trip

*For any* sequence of add, edit, and remove operations applied to a user's Platform_List, retrieving the Platform_List SHALL return exactly the set implied by those operations, and arbitrary free-text platform names SHALL be supported without error.

**Validates: Requirements 6.1, 6.2, 6.3, 6.4**

### Property 13: Session persistence round-trip

*For any* completed session (recommendation, feedback, mood pattern, context) stored via MemoryService, retrieving session data for the same user SHALL return matching values.

**Validates: Requirements 8.1, 8.2**

### Property 14: Empty platform list blocks recommendation

*For any* recommendation request where the Platform_List is empty, the agent SHALL return a response prompting the user to add at least one Owned_Platform and SHALL NOT produce a primary recommendation.

**Validates: Requirements 6.5**

### Property 15: Every recommendation is playable on an owned platform

*For any* combination of library records, owned platforms, mood, and time budget, every game returned by the Recommender (primary and all alternatives, 0–3) SHALL have confirmed platform availability intersecting the user's Platform_List, each with non-empty reasoning.

**Validates: Requirements 5.3, 7.1, 7.4**

### Property 16: Unconfirmed availability is never the primary recommendation

*For any* candidate whose platform availability is empty or unconfirmed, that candidate SHALL be excluded from the primary recommendation.

**Validates: Requirements 7.5**

### Property 17: Community review quality drives ranking

*For any* set of candidates equally eligible on mood, time budget, and owned-platform constraints, the primary recommendation SHALL have the maximum community review score among them, and any candidate ordering SHALL be non-increasing in review score (candidates lacking review data ranked below any reviewed candidate).

**Validates: Requirements 7.2**

### Property 18: Primary reasoning includes a community review summary

*For any* primary recommendation whose record has community review data, the `reasoning` SHALL include the review's sentiment summary; *for any* primary lacking review data, the reasoning SHALL indicate community review data is unavailable.

**Validates: Requirements 7.3**

### Property 19: Time budget constraint on recommendations

*For any* recommendation generated by the Recommender, the recommended game's `estimated_playtime` SHALL be less than or equal to the user's `time_budget_minutes`.

**Validates: Requirements 7.1**

### Property 20: No repeat recommendations in recent history

*For any* user with a non-empty recommendation history, the Recommender SHALL not produce a primary recommendation matching any game recommended in the most recent 5 sessions, unless the user explicitly requests a re-recommendation.

**Validates: Requirements 8.3**

### Property 21: Tavily rate-limit compliance

*For any* sequence of Tavily calls (enrichment or autocomplete), TavilyService SHALL not exceed the free-tier limit (60 requests per minute); any call that would exceed the limit SHALL return empty results rather than calling the API.

**Validates: Requirements 5.4**

### Property 22: Error messages never expose technical details

*For any* exception raised by an external service (AgentCore Memory, Tavily, Xbox, or Gmail), the message shown to the user SHALL not contain stack traces, API keys, endpoint URLs, or internal error codes.

**Validates: Requirements 10.1, 10.4**
