"""Application wiring: build the agent + service graph from configuration.

A single place that constructs the real service graph (Bedrock model, Tavily,
DynamoDB-backed memory, record sources, library assembly, the tool registry, and
the agent runtime) from a :class:`~config.Config`. The future Streamlit UI and
the headless CLI both build on this, so the wiring lives in one place.

The Gmail source is included only when Gmail is configured; manual entry is
always available. Sources are passed in precedence order (Gmail -> manual).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.enricher import Enricher
from agent.library_service import LibraryService
from agent.runtime import AgentRuntime
from agent.tools import ToolRegistry
from config import Config
from services.bedrock_service import BedrockService
from services.dynamodb_memory_client import DynamoDBMemoryClient
from services.memory_service import MemoryService
from services.sources.base import RecordSource
from services.sources.gmail_source import GmailSource
from services.sources.manual_source import ManualSource
from services.tavily_service import TavilyService


@dataclass
class AppContext:
    """The wired application graph for one user."""

    config: Config
    user_id: str
    memory: MemoryService
    tavily: TavilyService
    library: LibraryService
    runtime: AgentRuntime
    gmail: GmailSource | None


def build_app(config: Config, user_id: str = "default") -> AppContext:
    """Construct the full service graph for ``user_id`` from ``config``."""
    bedrock = BedrockService(config)
    tavily = TavilyService(config.tavily_api_key)
    memory = MemoryService(
        DynamoDBMemoryClient(config.dynamodb_table_name, region_name=config.aws_region)
    )

    gmail = _build_gmail_source(config)
    sources: list[RecordSource] = []
    if gmail is not None:
        sources.append(gmail)  # Gmail takes precedence over manual entries
    sources.append(ManualSource(memory, user_id))

    enricher = Enricher(bedrock, tavily)
    library = LibraryService(sources=sources, enricher=enricher, memory=memory)
    tools = ToolRegistry(
        memory=memory, library=library, tavily=tavily, enricher=enricher, user_id=user_id
    )
    runtime = AgentRuntime(bedrock=bedrock, tools=tools, memory=memory)

    return AppContext(
        config=config,
        user_id=user_id,
        memory=memory,
        tavily=tavily,
        library=library,
        runtime=runtime,
        gmail=gmail,
    )


def _build_gmail_source(config: Config) -> GmailSource | None:
    """Build the Gmail source when a cached token is configured, else ``None`` (Req 3.6)."""
    if not config.gmail_enabled or config.gmail_token_path is None:
        return None
    return GmailSource(token_path=config.gmail_token_path)
