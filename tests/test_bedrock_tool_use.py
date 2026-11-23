"""Unit tests for BedrockService Converse parsing (no network).

Covers the tool-use turn parsing and the conversational path with a fake
``bedrock-runtime`` client: thinking blocks kept whole (signature included) so
they can be echoed back, a block the SDK cannot represent dropped so it never is,
and sanitized error propagation (no technical details leak).
"""

from __future__ import annotations

from typing import Any

import pytest

from config import Config
from services import bedrock_service
from services.bedrock_service import (
    BedrockService,
    BedrockServiceError,
    ConverseResult,
    StillThinking,
    ToolUse,
)

CONFIG = Config(
    aws_region="eu-north-1",
    bedrock_model_id="eu.anthropic.claude-sonnet-4-6",
    brave_api_key="secret",
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


_THINKING = {
    "reasoningContent": {"reasoningText": {"text": "Check the price.", "signature": "s1g"}}
}


def test_converse_tools_keeps_thinking_to_echo_back() -> None:
    """The next round of the loop must carry this turn's thinking, unchanged."""
    response = {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "content": [
                    _THINKING,
                    {"text": "Checking."},
                    {"toolUse": {"toolUseId": "t1", "name": "store_prices", "input": {}}},
                ]
            }
        },
    }
    result = _service(response).converse_tools([], [], "sys")

    assert result.text == "Checking."  # thinking is never shown
    assert result.assistant_content[0] == _THINKING
    assert len(result.assistant_content) == 3


_CACHE_POINT = {"cachePoint": {"type": "default"}}


def test_converse_tools_sends_system_tool_config_and_cache_points() -> None:
    response = {
        "stopReason": "end_turn",
        "output": {"message": {"content": [{"text": "ok"}]}},
    }
    svc = _service(response)
    client = svc._client
    history = [{"role": "user", "content": [{"text": "hi"}]}]
    svc.converse_tools(history, [{"toolSpec": {}}], "sys")
    # Static cache point after the system prompt; moving one at the end of the
    # last message — so each tool round re-reads the previous round's prefix.
    assert client.last_kwargs["system"] == [{"text": "sys"}, _CACHE_POINT]
    assert client.last_kwargs["messages"][-1]["content"] == [{"text": "hi"}, _CACHE_POINT]
    assert client.last_kwargs["toolConfig"] == {"tools": [{"toolSpec": {}}]}
    # Adaptive thinking gives the model a private place to reason, so its working
    # notes stop leading the answer.
    assert client.last_kwargs["additionalModelRequestFields"] == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low"},
    }
    assert client.last_kwargs["inferenceConfig"] == {"maxTokens": 16_000}
    # The caller's history is NEVER mutated: cache points are request-time only.
    assert history == [{"role": "user", "content": [{"text": "hi"}]}]


def test_converse_tools_degrades_when_cache_points_are_rejected() -> None:
    """A model/region without prompt caching flips the service to uncached calls
    for the rest of the process instead of failing the turn."""

    class _RejectsCacheOnce:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def converse(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            if any("cachePoint" in block for block in kwargs["system"]):
                raise RuntimeError("ValidationException: cachePoint is not supported")
            return {"stopReason": "end_turn", "output": {"message": {"content": [{"text": "ok"}]}}}

    client = _RejectsCacheOnce()
    svc = BedrockService(CONFIG, client=client)

    first = svc.converse_tools([{"role": "user", "content": [{"text": "hi"}]}], [], "sys")
    assert first.text == "ok"
    assert len(client.calls) == 2  # cached attempt, then the uncached retry

    svc.converse_tools([{"role": "user", "content": [{"text": "again"}]}], [], "sys")
    assert len(client.calls) == 3  # no cached attempt the second time
    assert client.calls[-1]["system"] == [{"text": "sys"}]


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


# --- converse_tools_stream (ConverseStream re-assembly) ---


class _FakeStreamClient:
    """Returns a preset ConverseStream event sequence or raises a preset error."""

    def __init__(
        self, events: list[dict[str, Any]] | None = None, error: Exception | None = None
    ) -> None:
        self._events = events or []
        self._error = error
        self.calls: list[dict[str, Any]] = []

    def converse_stream(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return {"stream": iter(self._events)}


def _stream_service(
    events: list[dict[str, Any]] | None = None, error: Exception | None = None
) -> tuple[BedrockService, _FakeStreamClient]:
    client = _FakeStreamClient(events, error)
    return BedrockService(CONFIG, client=client), client


def test_stream_yields_deltas_then_assembled_result() -> None:
    events: list[dict[str, Any]] = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Play "}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hades."}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 1,
                "start": {"toolUse": {"toolUseId": "t1", "name": "get_library"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"ge'}}}},
        {
            "contentBlockDelta": {
                "contentBlockIndex": 1,
                "delta": {"toolUse": {"input": 'nre": "RPG"}'}},
            }
        },
        {"contentBlockStop": {"contentBlockIndex": 1}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 10, "outputTokens": 5, "latencyMs": "ignored"}}},
    ]
    svc, _ = _stream_service(events)
    items = list(svc.converse_tools_stream([], [], "sys"))
    assert items[:2] == ["Play ", "Hades."]
    result = items[-1]
    assert isinstance(result, ConverseResult)
    assert result.stop_reason == "tool_use"
    assert result.text == "Play Hades."
    assert result.tool_uses == [ToolUse("t1", "get_library", {"genre": "RPG"})]
    # assistant_content mirrors the non-streaming parse, in block order.
    assert result.assistant_content == [
        {"text": "Play Hades."},
        {"toolUse": {"toolUseId": "t1", "name": "get_library", "input": {"genre": "RPG"}}},
    ]
    assert result.usage == {"inputTokens": 10, "outputTokens": 5}


def _thinking_delta(index: int, **part: Any) -> dict[str, Any]:
    return {"contentBlockDelta": {"contentBlockIndex": index, "delta": {"reasoningContent": part}}}


def test_stream_rebuilds_thinking_whole_and_never_yields_it() -> None:
    """Thinking streams in fragments with its signature last; it goes back whole and
    first, ahead of the text and tool call, and none of it reaches the user."""
    events: list[dict[str, Any]] = [
        _thinking_delta(0, text="Check "),
        _thinking_delta(0, text="the price."),
        _thinking_delta(0, signature="s1g"),
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"text": "Checking."}}},
        {
            "contentBlockStart": {
                "contentBlockIndex": 2,
                "start": {"toolUse": {"toolUseId": "t1", "name": "store_prices"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 2, "delta": {"toolUse": {"input": "{}"}}}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]
    svc, _ = _stream_service(events)
    items = list(svc.converse_tools_stream([], [], "sys"))

    assert items[:-1] == ["Checking."]
    result = items[-1]
    assert isinstance(result, ConverseResult)
    assert result.text == "Checking."
    assert result.assistant_content == [
        _THINKING,
        {"text": "Checking."},
        {"toolUse": {"toolUseId": "t1", "name": "store_prices", "input": {}}},
    ]


def test_stream_signals_life_while_the_model_thinks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thinking is not streamed; a StillThinking goes out instead, so the caller can
    keep a quiet connection alive. Throttled to one per _KEEPALIVE_SECONDS (0 here)."""
    monkeypatch.setattr(bedrock_service, "_KEEPALIVE_SECONDS", 0.0)
    events: list[dict[str, Any]] = [
        _thinking_delta(0, text="Hmm, "),
        _thinking_delta(0, text="the price."),
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"text": "Play Hades."}}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    svc, _ = _stream_service(events)
    items = list(svc.converse_tools_stream([], [], "sys"))

    assert items[:3] == [StillThinking(), StillThinking(), "Play Hades."]


def test_stream_returns_redacted_thinking_as_it_came() -> None:
    events: list[dict[str, Any]] = [
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"reasoningContent": {"redactedContent": b"\x00enc"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 1, "delta": {"text": "Play Hades."}}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]
    svc, _ = _stream_service(events)
    result = list(svc.converse_tools_stream([], [], "sys"))[-1]

    assert isinstance(result, ConverseResult)
    assert result.assistant_content[0] == {"reasoningContent": {"redactedContent": b"\x00enc"}}


def test_stream_defaults_missing_stop_reason_and_bad_tool_input() -> None:
    events = [
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "t1", "name": "get_library"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{not-"}}}},
    ]
    svc, _ = _stream_service(events)
    result = list(svc.converse_tools_stream([], [], "sys"))[-1]
    assert isinstance(result, ConverseResult)
    assert result.stop_reason == "end_turn"  # stream ended without messageStop
    assert result.tool_uses == [ToolUse("t1", "get_library", {})]  # unparseable input -> {}


def test_stream_degrades_when_cache_points_are_rejected() -> None:
    class _RejectsCachedStream:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def converse_stream(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            if any("cachePoint" in block for block in kwargs["system"]):
                raise RuntimeError("ValidationException: cachePoint is not supported")
            return {
                "stream": iter(
                    [
                        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "ok"}}},
                        {"messageStop": {"stopReason": "end_turn"}},
                    ]
                )
            }

    client = _RejectsCachedStream()
    svc = BedrockService(CONFIG, client=client)
    result = list(
        svc.converse_tools_stream([{"role": "user", "content": [{"text": "hi"}]}], [], "s")
    )[-1]
    assert isinstance(result, ConverseResult)
    assert result.text == "ok"
    assert len(client.calls) == 2  # cached attempt, then the uncached retry
    assert client.calls[-1]["system"] == [{"text": "s"}]


def test_stream_sanitizes_mid_stream_error() -> None:
    def _broken() -> Any:
        yield {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Pla"}}}
        raise RuntimeError("arn:aws:secret-endpoint exploded")

    class _MidStreamError:
        def converse_stream(self, **kwargs: Any) -> dict[str, Any]:
            return {"stream": _broken()}

    svc = BedrockService(CONFIG, client=_MidStreamError())
    stream = svc.converse_tools_stream([], [], "sys")
    assert next(stream) == "Pla"  # deltas before the break still arrive
    with pytest.raises(BedrockServiceError) as exc:
        list(stream)
    assert "secret-endpoint" not in str(exc.value)


def test_stream_sanitizes_request_error() -> None:
    svc, _ = _stream_service(error=RuntimeError("AccessDen/arn:aws:secret-endpoint"))
    with pytest.raises(BedrockServiceError) as exc:
        list(svc.converse_tools_stream([], [], "sys"))
    assert "secret-endpoint" not in str(exc.value)


def test_converse_tools_extracts_usage_counters() -> None:
    response = {
        "stopReason": "end_turn",
        "output": {"message": {"content": [{"text": "ok"}]}},
        "usage": {
            "inputTokens": 12,
            "outputTokens": 34,
            "totalTokens": 46,
            "cacheReadInputTokens": 500,
            "cacheWriteInputTokens": 100,
            "someFutureField": "ignored",
        },
    }
    result = _service(response).converse_tools([], [], "sys")
    assert result.usage == {
        "inputTokens": 12,
        "outputTokens": 34,
        "totalTokens": 46,
        "cacheReadInputTokens": 500,
        "cacheWriteInputTokens": 100,
    }
