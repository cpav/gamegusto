"""AWS Bedrock base-model client (Converse API with extended thinking).

Boundary to the Claude base model on Amazon Bedrock. Uses the Converse API
(``bedrock-runtime``) with extended thinking enabled so the model reasons before
answering. Two invocation styles are exposed: ``invoke_with_schema`` for
structured JSON output (used by the MoodInterpreter to map free text to mood
dimensions, Req 1.2) and ``invoke_conversational`` for free-text replies (used
by the Recommender to generate reasoning, Req 7.2).

The LLM is a hard dependency: any failure raises ``BedrockServiceError`` with a
sanitized message. Callers do not silently fall back to mock/deterministic
output — a misconfigured or unavailable model surfaces as an error.
"""

from __future__ import annotations

import json
from typing import Any

import boto3

from config import Config
from services.error_handler import ErrorHandler

# Output-token headroom added on top of the reasoning budget so the model has
# room to emit a final answer after its thinking block (Converse ``maxTokens``
# counts thinking + answer tokens).
_ANSWER_TOKEN_HEADROOM = 4096


class BedrockServiceError(RuntimeError):
    """Raised when a Bedrock invocation fails; message is already sanitized."""


class BedrockService:
    """Invokes a Bedrock base model via Converse with extended thinking."""

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

    def invoke_with_schema(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Invoke the model and parse its reply into a dict matching ``schema``.

        Raises ``BedrockServiceError`` (sanitized) on transport failure or when
        the reply contains no valid JSON object.
        """
        raw = self._converse(self._schema_prompt(prompt, schema))
        return self._parse_json(raw)

    def invoke_conversational(self, prompt: str, session_id: str) -> str:
        """Return the model's free-text response for ``prompt``.

        ``session_id`` is accepted for call-site symmetry; the Converse call is
        stateless and the application supplies any needed context in ``prompt``.
        Raises ``BedrockServiceError`` (sanitized) on failure.
        """
        return self._converse(prompt)

    def _converse(self, prompt: str) -> str:
        """Call the Converse API with extended thinking and return the answer text."""
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
