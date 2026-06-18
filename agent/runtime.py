"""Agent runtime: the tool-use conversation loop.

Replaces the old fixed mood -> time -> recommend state machine. The Claude Sonnet
model on Bedrock is the agent: it interprets each message, decides which tools to
call (platforms, library, enrichment, web search, persistence), asks for missing
information only when needed, and produces a recommendation that honors what the
user actually asked for. Clarifying questions, recommendations, and follow-ups
("something else", "shorter", "I already played it") are all just turns in one
conversation, so the running ``messages`` history is the entire state.

The LLM is a hard dependency: a Bedrock failure propagates as
``BedrockServiceError`` (sanitized) rather than degrading to canned output.
Memory and Tavily degrade gracefully via the tools they back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.tools import ToolRegistry
from services.bedrock_service import BedrockService
from services.memory_service import MemoryService

#: Hard cap on tool-call rounds per user turn, so a misbehaving loop terminates.
_MAX_TOOL_ITERATIONS = 8

#: Fallback shown if the model never settles on a final answer within the cap.
_ITERATION_LIMIT_MESSAGE = (
    "I'm having trouble pulling this together right now — could you rephrase or "
    "give me a bit more to go on?"
)

SYSTEM_PROMPT = """\
You are GameGusto, a friendly expert that recommends the single best video game \
for the user to play next. You converse naturally — there is no fixed script.

How you work:
- You decide what to do. Use the tools to read and change data (owned platforms, \
the game library, enrichment, web search, saving the recommendation). Reason \
about the user's mood and available time yourself; do not ask for them as a rigid \
questionnaire. Ask a clarifying question only when you genuinely cannot proceed.
- Honor the WHOLE request. If the user wants "an RPG with a job system, 2D HD, \
solo, challenging, ~30h", every part matters — never recommend something that \
ignores the stated taste or genre.
- You choose the game, not a tool. Call get_library to see what the user owns, \
then pick using the user's taste + mood + time + owned platforms. Use your own \
knowledge of these titles; treat enrichment 'genre' as a weak hint and prefer \
what you actually know. Call enrich_game or web_search to fill real gaps.
- Owned platforms: call get_owned_platforms first. If the list is empty, ask the \
user which platforms they own (offer add_platform) before recommending. Match \
platforms by family: owning "Xbox" covers "Xbox Series X/S" and "Xbox One"; \
"Switch" covers "Nintendo Switch"; "PC" covers Steam/Windows.
- Playtime: estimated_playtime is usually a game's full completion time, not a \
session length. Don't reject a 40h game because the user has 2 hours tonight — \
reason about whether it plays well in short sessions, and say so.
- Recommend ONE primary game with clear reasoning (why it fits mood, time, taste, \
and platform, plus a note on community reception when known) and up to THREE \
alternatives with brief reasons.
- Follow-ups: within this conversation, remember what you've already suggested. \
On "I already played it" / "something else" / "shorter", exclude the prior pick \
and offer the next best WITHOUT re-asking everything you already know.
- Across sessions, call get_recent_recommendations to avoid repeating recent \
picks unless the user asks to revisit one.
- After you present a recommendation, call save_recommendation once to persist it.
- If a tool reports an error or returns nothing, adapt and be honest about what \
you couldn't verify; never invent ratings, platforms, or titles the user doesn't \
own.
"""


@dataclass
class AgentReply:
    """One agent turn for the caller (CLI/UI) to render."""

    message: str
    is_stateless_mode: bool = False
    """True when the memory store is unavailable, so personalization is limited."""

    tool_calls: list[str] = field(default_factory=list)
    """Names of tools invoked while producing this reply (for transparency)."""


class AgentRuntime:
    """Drives the Bedrock tool-use loop for one user's conversation."""

    def __init__(
        self,
        bedrock: BedrockService,
        tools: ToolRegistry,
        memory: MemoryService,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        """Build the runtime around the model, the tool registry, and memory."""
        self._bedrock = bedrock
        self._tools = tools
        self._memory = memory
        self._system = system_prompt
        self._messages: list[dict[str, Any]] = []

    def reset(self) -> None:
        """Clear the conversation history to start a fresh session."""
        self._messages = []

    def send(self, user_text: str) -> AgentReply:
        """Send one user message and run the tool loop until a final answer.

        Raises ``BedrockServiceError`` (sanitized) if the model is unavailable —
        the LLM is a hard dependency with no canned fallback.
        """
        self._messages.append({"role": "user", "content": [{"text": user_text}]})
        called: list[str] = []
        # Collect text from every turn, not just the final one: the model often
        # writes its recommendation prose in the same turn it calls a tool (e.g.
        # save_recommendation) and then ends with only a short closing line, so
        # returning just the last turn's text would drop the actual answer.
        texts: list[str] = []

        for _ in range(_MAX_TOOL_ITERATIONS):
            result = self._bedrock.converse_tools(self._messages, self._tools.specs(), self._system)
            if result.text.strip():
                texts.append(result.text.strip())
            if result.assistant_content:
                self._messages.append({"role": "assistant", "content": result.assistant_content})

            if result.stop_reason != "tool_use" or not result.tool_uses:
                return self._reply("\n\n".join(texts), called)

            tool_results: list[dict[str, Any]] = []
            for use in result.tool_uses:
                called.append(use.name)
                output = self._tools.dispatch(use.name, use.input)
                tool_results.append(
                    {
                        "toolResult": {
                            "toolUseId": use.tool_use_id,
                            "content": [{"json": output}],
                            "status": "success",
                        }
                    }
                )
            self._messages.append({"role": "user", "content": tool_results})

        return self._reply("\n\n".join(texts), called)

    def _reply(self, message: str, called: list[str]) -> AgentReply:
        """Wrap ``message`` in an :class:`AgentReply`, reflecting memory health."""
        return AgentReply(
            message=message or _ITERATION_LIMIT_MESSAGE,
            is_stateless_mode=not self._memory.is_available,
            tool_calls=called,
        )
