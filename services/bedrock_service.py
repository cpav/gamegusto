"""AWS Bedrock base-model client (Converse API).

Boundary to the Claude Sonnet base model on Amazon Bedrock via the Converse API
(``bedrock-runtime``). Two entry points are exposed:

* :meth:`invoke_conversational` — a single-shot free-text reply with extended
  thinking enabled (used by the live healthcheck).
* :meth:`converse_tools` — one turn of a tool-use loop. The agent runtime
  (:mod:`agent.runtime`) drives the loop: it passes the running ``messages``
  history plus the tool specs, this method performs one ``converse`` call, and
  the runtime executes any requested tools and calls again until the model emits
  a final answer.

Extended thinking is intentionally **not** enabled for the tool-use turn:
interleaved ``reasoningContent`` blocks returned alongside tool use must be
echoed back verbatim on the next turn, which the pinned boto3 cannot round-trip
(it surfaces them as ``SDK_UNKNOWN_MEMBER``). Running the loop without thinking
keeps the message history clean and portable; the model still reasons strongly
over the tools.

The LLM is a hard dependency: any failure raises ``BedrockServiceError`` with a
sanitized message. Callers never fall back to mock/deterministic output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import boto3

from config import Config
from services.error_handler import ErrorHandler

# Output-token headroom added on top of the reasoning budget so the model has
# room to emit an answer (Converse ``maxTokens`` counts thinking + answer tokens).
_ANSWER_TOKEN_HEADROOM = 4096


class BedrockServiceError(RuntimeError):
    """Raised when a Bedrock invocation fails; message is already sanitized."""


@dataclass
class ToolUse:
    """A single tool the model asked to call during a tool-use turn."""

    tool_use_id: str
    name: str
    input: dict[str, Any]


@dataclass
class ConverseResult:
    """The outcome of one :meth:`BedrockService.converse_tools` turn.

    ``assistant_content`` is the raw Converse content-block list, returned so the
    runtime can append the assistant message back onto the history verbatim
    before sending tool results.
    """

    stop_reason: str
    text: str
    tool_uses: list[ToolUse] = field(default_factory=list)
    assistant_content: list[dict[str, Any]] = field(default_factory=list)


class BedrockService:
    """Invokes a Bedrock base model via the Converse API."""

    def __init__(self, config: Config, client: Any | None = None) -> None:
        """Build the service from ``config``; inject ``client`` for testing.

        When ``client`` is omitted, a ``bedrock-runtime`` boto3 client is created
        for the configured region.
        """
        self._model_id = config.bedrock_model_id
        self._reasoning_budget = config.bedrock_reasoning_budget_tokens
        self._client = (
            client
            if client is not None
            else boto3.client("bedrock-runtime", region_name=config.aws_region)
        )

    def invoke_conversational(self, prompt: str, session_id: str) -> str:
        """Return the model's free-text response for ``prompt`` (extended thinking on).

        ``session_id`` is accepted for call-site symmetry; the Converse call is
        stateless. Raises ``BedrockServiceError`` (sanitized) on failure.
        """
        try:
            response = self._client.converse(
                modelId=self._model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": self._reasoning_budget + _ANSWER_TOKEN_HEADROOM},
                additionalModelRequestFields={
                    "thinking": {"type": "enabled", "budget_tokens": self._reasoning_budget}
                },
            )
        except Exception as exc:
            raise BedrockServiceError(ErrorHandler.sanitize_error(exc, "llm")) from exc
        return self._extract_text(response)

    def converse_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> ConverseResult:
        """Run one Converse turn with ``tools`` available, returning the result.

        ``messages`` is the running conversation history (user/assistant turns,
        including any prior ``toolResult`` blocks); ``tools`` is the Converse
        ``toolSpec`` list; ``system`` is the system prompt. Raises
        ``BedrockServiceError`` (sanitized) on transport failure.
        """
        try:
            response = self._client.converse(
                modelId=self._model_id,
                system=[{"text": system}],
                messages=messages,
                toolConfig={"tools": tools},
                inferenceConfig={"maxTokens": self._reasoning_budget + _ANSWER_TOKEN_HEADROOM},
            )
        except Exception as exc:
            raise BedrockServiceError(ErrorHandler.sanitize_error(exc, "llm")) from exc
        return self._parse_tool_turn(response)

    @staticmethod
    def _parse_tool_turn(response: dict[str, Any]) -> ConverseResult:
        """Parse a Converse tool-use response into a :class:`ConverseResult`.

        Keeps only well-formed ``text``/``toolUse`` blocks in ``assistant_content``
        so any model-internal block the SDK cannot represent is never echoed back.
        """
        try:
            stop_reason = response["stopReason"]
            blocks = response["output"]["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise BedrockServiceError(ErrorHandler.GENERIC_MESSAGES["llm_unavailable"]) from exc

        text_parts: list[str] = []
        tool_uses: list[ToolUse] = []
        assistant_content: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if isinstance(block.get("text"), str):
                text_parts.append(block["text"])
                assistant_content.append({"text": block["text"]})
            elif isinstance(block.get("toolUse"), dict):
                use = block["toolUse"]
                tool_uses.append(
                    ToolUse(
                        tool_use_id=use["toolUseId"],
                        name=use["name"],
                        input=use.get("input") or {},
                    )
                )
                assistant_content.append({"toolUse": use})

        return ConverseResult(
            stop_reason=stop_reason,
            text="".join(text_parts),
            tool_uses=tool_uses,
            assistant_content=assistant_content,
        )

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        """Concatenate the answer text blocks, ignoring thinking/reasoning blocks.

        Raises ``BedrockServiceError`` when the response carries no answer text.
        """
        try:
            blocks = response["output"]["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise BedrockServiceError(ErrorHandler.GENERIC_MESSAGES["llm_unavailable"]) from exc
        text = "".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
        if not text.strip():
            raise BedrockServiceError(ErrorHandler.GENERIC_MESSAGES["llm_unavailable"])
        return text
