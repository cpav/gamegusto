"""Wiring tests for :func:`bootstrap.build_app` (no network).

Construction must not touch the network: boto3 and Tavily clients are created
lazily/offline, so building the graph for a test config yields a fully wired
``AppContext`` with the agent runtime and shared services in place.
"""

from __future__ import annotations

from agent.runtime import AgentRuntime
from bootstrap import build_app
from config import Config

CONFIG = Config(
    aws_region="eu-north-1",
    bedrock_model_id="eu.anthropic.claude-sonnet-4-6",
    tavily_api_key="x",
    dynamodb_table_name="gamegusto",
)


def test_build_app_wires_runtime_without_gmail() -> None:
    ctx = build_app(CONFIG, user_id="u1")

    assert ctx.user_id == "u1"
    assert isinstance(ctx.runtime, AgentRuntime)
    assert ctx.memory is not None
    assert ctx.tavily is not None
    assert ctx.library is not None
    assert ctx.gmail is None  # Gmail not configured -> source omitted (Req 3.6)


def test_build_app_defaults_user_id() -> None:
    ctx = build_app(CONFIG)
    assert ctx.user_id == "default"


def _with_region(region: str | None) -> Config:
    return Config(
        aws_region="eu-north-1",
        bedrock_model_id="m",
        tavily_api_key="x",
        dynamodb_table_name="t",
        deals_region=region,
    )


def test_region_resolves_config_then_detected_then_default() -> None:
    # Explicit config region wins over a detected one, and reaches the prompt.
    ctx = build_app(_with_region("France"), detected_region="Sweden")
    assert "based in France" in ctx.runtime._system  # noqa: SLF001 - wiring under test

    # Detected region is used when config leaves it unset.
    ctx = build_app(_with_region(None), detected_region="Sweden")
    assert "based in Sweden" in ctx.runtime._system  # noqa: SLF001

    # Falls back to the default when neither is given.
    ctx = build_app(_with_region(None))
    assert "based in Denmark" in ctx.runtime._system  # noqa: SLF001
