"""Interpret a user's free-text Mood_Input into internal mood dimensions.

Maps free text to four mood dimensions via the Bedrock AgentCore agent and
flags a clarifying follow-up when the mood cannot be interpreted (Requirements
1.1, 1.2, 1.3). External failures degrade to a clarification request rather than
propagating to the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.bedrock_service import BedrockService

#: Mood dimensions requested from the agent; each clamped to [0.0, 1.0].
_DIMENSION_FIELDS = ("energy_level", "stress_level", "social_desire", "challenge_appetite")

#: Friendly follow-up used whenever the mood cannot be interpreted (Req 1.3).
DEFAULT_CLARIFICATION_QUESTION = (
    "I couldn't quite read your mood there. How are you feeling right now? "
    "For example, are you energized or worn out, relaxed or stressed?"
)

#: JSON schema describing the four dimensions plus the interpretability flag.
_MOOD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "energy_level": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "stress_level": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "social_desire": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "challenge_appetite": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "interpretable": {
            "type": "boolean",
            "description": "False when the text does not convey a discernible mood.",
        },
        "clarification_question": {
            "type": "string",
            "description": "A friendly follow-up to ask when interpretable is false.",
        },
    },
    "required": ["interpretable"],
}


@dataclass
class MoodDimensions:
    """Internal mood representation; each dimension is a float in [0.0, 1.0]."""

    energy_level: float
    stress_level: float
    social_desire: float
    challenge_appetite: float


@dataclass
class MoodInterpretation:
    """Outcome of interpreting a Mood_Input: dimensions, or a clarification ask."""

    mood_dimensions: MoodDimensions | None
    needs_clarification: bool
    clarification_question: str | None


class MoodInterpreter:
    """Map free-text mood to dimensions using the Bedrock AgentCore agent."""

    def __init__(self, bedrock: BedrockService) -> None:
        """Build the interpreter; ``bedrock`` provides structured JSON output."""
        self._bedrock = bedrock

    def interpret(self, text: str) -> MoodInterpretation:
        """Map free-text mood to dimensions; flag clarification when unclear (Req 1.2, 1.3).

        The LLM is a hard dependency: a Bedrock failure propagates as
        ``BedrockServiceError`` rather than degrading to a canned response. A
        clarification is returned only when the model itself reports the mood is
        uninterpretable or omits required dimensions (Req 1.3).
        """
        data = self._bedrock.invoke_with_schema(self._prompt(text), _MOOD_SCHEMA)
        return self._build(data)

    def _build(self, data: dict[str, Any]) -> MoodInterpretation:
        """Validate the agent payload into dimensions or a clarification ask (Req 1.2, 1.3)."""
        if data.get("interpretable") is False:
            return self._clarify(data.get("clarification_question"))
        dimensions = self._extract_dimensions(data)
        if dimensions is None:
            return self._clarify(data.get("clarification_question"))
        return MoodInterpretation(
            mood_dimensions=dimensions,
            needs_clarification=False,
            clarification_question=None,
        )

    @staticmethod
    def _extract_dimensions(data: dict[str, Any]) -> MoodDimensions | None:
        """Read the four dimensions, clamping to [0, 1]; None if any is missing/non-numeric."""
        values: list[float] = []
        for field in _DIMENSION_FIELDS:
            raw = data.get(field)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return None
            values.append(min(1.0, max(0.0, float(raw))))
        return MoodDimensions(*values)

    @staticmethod
    def _clarify(question: str | None = None) -> MoodInterpretation:
        """Build a clarification result, falling back to the default question (Req 1.3)."""
        return MoodInterpretation(
            mood_dimensions=None,
            needs_clarification=True,
            clarification_question=question or DEFAULT_CLARIFICATION_QUESTION,
        )

    @staticmethod
    def _prompt(text: str) -> str:
        """Build the extraction prompt instructing the agent how to score the mood."""
        return (
            "You map a player's described mood to four numeric dimensions, each "
            "between 0.0 and 1.0: energy_level (worn out -> energized), stress_level "
            "(calm -> stressed), social_desire (solo -> craving company), and "
            "challenge_appetite (relaxing -> craving a challenge). If the text does "
            "not convey a discernible mood, set interpretable to false and supply a "
            "friendly clarification_question. Otherwise set interpretable to true and "
            "provide all four dimensions.\n\n"
            f"Player's mood: {text}"
        )
