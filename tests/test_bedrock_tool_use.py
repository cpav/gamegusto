"""Unit tests for BedrockService Converse parsing (no network).

Covers the new tool-use turn parsing and the existing conversational path with a
fake ``bedrock-runtime`` client, including the interleaved-thinking block the
pinned SDK cannot represent (filtered so it is never echoed back) and sanitized
error propagation (no technical details leak).
"""

from __future__ import annotations

from typing import Any

import pytest

from config import Config
from services.bedrock_service import BedrockService, BedrockServiceError, ToolUse

CONFIG = Config(
    aws_region="eu-north-1",
    bedrock_model_id="eu.anthropic.claude-sonnet-4-6",
    tavily_api_key="secret",
    dynamodb_table_name="gamegusto",
)


class _FakeClient:
    """Returns a preset Converse response or raises a preset error."""

    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.last_kwargs: dict[str, Any] | None = None

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.last_kwargs = kwargs
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _service(
    response: dict[str, Any] | None = None, error: Exception | None = None
) -> BedrockService:
    return BedrockService(CONFIG, client=_FakeClient(response, error))


def test_converse_tools_parses_tool_use() -> None:
    response = {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "content": [
                    {"text": "Let me check."},
                    {
                        "toolUse": {
                            "toolUseId": "t1",
                            "name": "get_library",
                            "input": {"genre": "RPG"},
                        }
                    },
                ]
            }
        },
    }
    result = _service(response).converse_tools([], [], "sys")
    assert result.stop_reason == "tool_use"
    assert result.text == "Let me check."
    assert result.tool_uses == [ToolUse("t1", "get_library", {"genre": "RPG"})]
    # assistant_content round-trips both blocks verbatim for the next turn.
    assert result.assistant_content == [
        {"text": "Let me check."},
        {"toolUse": {"toolUseId": "t1", "name": "get_library", "input": {"genre": "RPG"}}},
    ]


def test_converse_tools_drops_unrepresentable_thinking_block() -> None:
    response = {
        "stopReason": "end_turn",
        "output": {
            "message": {
                "content": [
                    {"SDK_UNKNOWN_MEMBER": {"name": "reasoningContent"}},
                    {"text": "Play Hades."},
                ]
            }
        },
    }
    result = _service(response).converse_tools([], [], "sys")
    assert result.stop_reason == "end_turn"
    assert result.text == "Play Hades."
    assert result.tool_uses == []
    assert result.assistant_content == [{"text": "Play Hades."}]  # unknown block dropped


def test_converse_tools_sends_system_and_tool_config() -> None:
    response = {
        "stopReason": "end_turn",
        "output": {"message": {"content": [{"text": "ok"}]}},
    }
    svc = _service(response)
    client = svc._client
    svc.converse_tools([{"role": "user", "content": [{"text": "hi"}]}], [{"toolSpec": {}}], "sys")
    assert client.last_kwargs["system"] == [{"text": "sys"}]
    assert client.last_kwargs["toolConfig"] == {"tools": [{"toolSpec": {}}]}
    # The tool loop runs without extended thinking (no reasoningContent to echo back).
    assert "additionalModelRequestFields" not in client.last_kwargs


def test_converse_tools_sanitizes_transport_error() -> None:
    svc = _service(error=RuntimeError("AccessDen/arn:aws:secret-endpoint"))
    with pytest.raises(BedrockServiceError) as exc:
        svc.converse_tools([], [], "sys")
    assert "secret-endpoint" not in str(exc.value)
    assert "temporarily unavailable" in str(exc.value)


def test_converse_tools_rejects_malformed_response() -> None:
    with pytest.raises(BedrockServiceError):
        _service({"unexpected": True}).converse_tools([], [], "sys")


def test_invoke_conversational_returns_text() -> None:
    response = {"output": {"message": {"content": [{"text": "I am reachable."}]}}}
    assert _service(response).invoke_conversational("ping", "s") == "I am reachable."


def test_invoke_conversational_rejects_empty_text() -> None:
    response = {"output": {"message": {"content": [{"text": "   "}]}}}
    with pytest.raises(BedrockServiceError):
        _service(response).invoke_conversational("ping", "s")
