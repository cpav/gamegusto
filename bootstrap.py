"""Application wiring: build the agent + service graph from configuration.

A single place that constructs the real service graph (Bedrock model, Brave search,
DynamoDB-backed memory, record sources, library assembly, the tool registry, and
the agent runtime) from a :class:`~config.Config`. The future Streamlit UI and
the headless CLI both build on this, so the wiring lives in one place.

The Gmail source is included only when Gmail is configured; manual entry is
always available. Sources are passed in precedence order (Gmail -> manual).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from agent.enricher import Enricher
from agent.library_service import LibraryService
from agent.runtime import AgentRuntime, system_prompt_for_region
from agent.tools import ToolRegistry
from config import DEFAULT_DEALS_REGION, Config
from services.bedrock_service import BedrockService
from services.dynamodb_memory_client import DynamoDBMemoryClient
from services.igdb_service import IgdbService
from services.memory_service import MemoryService
from services.search_service import SearchService
from services.sources.base import RecordSource
from services.sources.manual_source import ManualSource

if TYPE_CHECKING:
    # Annotation only — see _build_gmail_source for why this is not a runtime
    # import. `from __future__ import annotations` keeps the hints as strings.
    from services.sources.gmail_source import GmailSource


@dataclass
class AppContext:
    """The wired application graph for one user."""

    config: Config
    user_id: str
    memory: MemoryService
    search: SearchService
    igdb: IgdbService
    library: LibraryService
    enricher: Enricher
    runtime: AgentRuntime
    gmail: GmailSource | None


def build_app(
    config: Config, user_id: str = "default", detected_region: str | None = None
) -> AppContext:
    """Construct the full service graph for ``user_id`` from ``config``.

    The store-deals region resolves as **explicit ``config.deals_region`` ›
    ``detected_region`` (e.g. browser timezone, passed by the UI) › default**, and is
    surfaced to the agent in the system prompt so it knows the region (and currency)
    without asking — the agent then reads deals itself via ``web_search`` (deep).
    """
    region = config.deals_region or detected_region or DEFAULT_DEALS_REGION
    bedrock = BedrockService(config)
    search = SearchService(config.brave_api_key)
    memory = MemoryService(
        DynamoDBMemoryClient(config.dynamodb_table_name, region_name=config.aws_region)
    )

    gmail = _build_gmail_source(config)
    sources: list[RecordSource] = []
    if gmail is not None:
        sources.append(gmail)  # Gmail takes precedence over manual entries
    sources.append(ManualSource(memory, user_id))

    igdb = IgdbService(config.igdb_client_id, config.igdb_client_secret)
    enricher = Enricher(bedrock, search, igdb)
    library = LibraryService(sources=sources, enricher=enricher, memory=memory)
    tools = ToolRegistry(
        memory=memory, library=library, search=search, enricher=enricher, user_id=user_id
    )
    runtime = AgentRuntime(
        bedrock=bedrock,
        tools=tools,
        memory=memory,
        # A callable so "today" is resolved at each turn, not baked in at build time —
        # a cached Streamlit session can outlive midnight, and the agent uses the date
        # to judge whether store deals are still live.
        system_prompt=lambda: system_prompt_for_region(region, today=date.today()),
    )

    return AppContext(
        config=config,
        user_id=user_id,
        memory=memory,
        search=search,
        igdb=igdb,
        library=library,
        enricher=enricher,
        runtime=runtime,
        gmail=gmail,
    )


def _build_gmail_source(config: Config) -> GmailSource | None:
    """Build the Gmail source when a cached token is configured, else ``None`` (Req 3.6).

    The import is deferred because Gmail is genuinely optional: the deployed
    API never uses it (the token stays on your machine), and importing at
    module scope would force the Google API client stack into the Lambda
    bundle to satisfy a module that is never called. A missing library is
    therefore treated the same as a missing token — the source is simply
    unavailable, and manual entry carries on.
    """
    if not config.gmail_enabled or config.gmail_token_path is None:
        return None

    try:
        from services.sources.gmail_source import GmailSource
    except ImportError:
        return None

    return GmailSource(token_path=config.gmail_token_path)
