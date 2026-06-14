"""AWS Bedrock AgentCore client.

Boundary to the conversational agent on Bedrock AgentCore. Exposes two
invocation styles: ``invoke_with_schema`` for structured JSON output (used by
the MoodInterpreter to map free text to mood dimensions, Req 1.2) and
``invoke_conversational`` for free-text replies (used by the Recommender to
generate reasoning, Req 7.2). External failures are routed through
``ErrorHandler`` so no technical details ever reach the caller.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import boto3

from config import Config
from services.error_handler import ErrorHandler


class BedrockServiceError(RuntimeError):
    """Raised when an AgentCore invocation fails; message is already sanitized."""


class BedrockService:
    """Invokes a Bedrock AgentCore agent for structured and conversational output."""

    def __init__(self, config: Config, client: Any | None = None) -> None:
        """Build the service from ``config``; inject ``client`` for testing.

        When ``client`` is omitted, a ``bedrock-agent-runtime`` boto3 client is
        created for the configured region.
        """
        self._agent_id = config.bedrock_agent_id
        self._agent_alias_id = config.bedrock_agent_alias_id
        self._client = (
            client
            if client is not None
            else boto3.client("bedrock-agent-runtime", region_name=config.aws_region)
        )

    def invoke_with_schema(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Invoke the agent and parse its reply into a dict matching ``schema``.

        A fresh session is used per call since structured extractions are
        stateless. Raises ``BedrockServiceError`` (sanitized) on failure.
        """
        raw = self._invoke(self._schema_prompt(prompt, schema), uuid.uuid4().hex)
        return self._parse_json(raw)

    def invoke_conversational(self, prompt: str, session_id: str) -> str:
        """Return the agent's free-text response within ``session_id``.

        Reusing a session id preserves conversational context across turns.
        Raises ``BedrockServiceError`` (sanitized) on failure.
        """
        return self._invoke(prompt, session_id)

    def _invoke(self, prompt: str, session_id: str) -> str:
        """Invoke the agent and concatenate the streamed completion chunks."""
        try:
            response = self._client.invoke_agent(
                agentId=self._agent_id,
                agentAliasId=self._agent_alias_id,
                sessionId=session_id,
                inputText=prompt,
            )
            chunks: list[str] = []
            for event in response["completion"]:
                chunk = event.get("chunk")
                if chunk and "bytes" in chunk:
                    chunks.append(chunk["bytes"].decode("utf-8"))
            return "".join(chunks)
        except Exception as exc:
            raise BedrockServiceError(ErrorHandler.sanitize_error(exc, "unknown")) from exc

    @staticmethod
    def _schema_prompt(prompt: str, schema: dict[str, Any]) -> str:
        """Augment ``prompt`` with an instruction to reply with schema-conforming JSON."""
        return (
            f"{prompt}\n\n"
            "Respond with a single JSON object only, with no surrounding prose, "
            f"conforming to this schema:\n{json.dumps(schema)}"
        )

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """Extract and parse the first JSON object from ``raw``.

        Raises ``BedrockServiceError`` (sanitized) when no valid JSON object is
        present, so malformed responses never surface as raw text.
        """
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            raise BedrockServiceError(ErrorHandler.GENERIC_MESSAGES["unknown"])
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise BedrockServiceError(ErrorHandler.sanitize_error(exc, "unknown")) from exc
        if not isinstance(parsed, dict):
            raise BedrockServiceError(ErrorHandler.GENERIC_MESSAGES["unknown"])
        return parsed
