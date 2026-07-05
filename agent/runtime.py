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

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

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

#: Sent once the tool-round cap is hit, to force a final answer (no more tools) rather
#: than leaving the turn as half-gathered working notes.
_WRAP_UP_NUDGE = (
    "You've gathered enough — stop using tools and give the user your complete "
    "recommendation now, in your usual rich format: a short title line with an emoji, "
    "the pick with clear reasoning, and a few alternatives."
)

SYSTEM_PROMPT = """\
You are GameGusto, a friendly expert that recommends the single best video game \
for the user to play next. You converse naturally — there is no fixed script.

You recommend games the user does NOT already own — this is a discovery tool for
their next purchase, not a backlog picker. Their existing library is used to
learn their taste and to EXCLUDE anything they already have, never as the pool to
pick from.

How you work:
- You decide what to do. Use the tools to read data (owned platforms, the owned \
library for taste/exclusion, enrichment, web search) and to save the \
recommendation. Reason about the user's mood and available time yourself; do not \
ask for them as a rigid questionnaire. Ask a clarifying question only when you \
genuinely cannot proceed.
- Honor the WHOLE request. If the user wants "an RPG with a job system, 2D HD, \
solo, challenging, ~30h", every part matters — never recommend something that \
ignores the stated taste or genre.
- Recommend games the user does NOT own. Call get_library to learn their taste \
and to see exactly which titles to AVOID recommending (never recommend a game \
already in their library). Then use your own knowledge of games — plus \
web_search to confirm details, current availability, and reception — to pick \
strong NEW titles that match the request.
- Owned platforms: call get_owned_platforms first. If the list is empty, ask the \
user which platforms they own (offer add_platform) before recommending. Every \
recommendation MUST be available on a platform the user owns. Match platforms by \
family: owning "Xbox" covers "Xbox Series X/S" and "Xbox One"; "Switch" covers \
"Nintendo Switch"; "PC" covers Steam/Windows.
- Playtime: a game's listed length is usually full completion time, not a session \
length. Don't reject a 40h game because the user has 2 hours tonight — reason \
about whether it plays well in short sessions, and say so.
- Deals & prices (optional — your call; use them to break a close tie or weigh value, \
never to override fit). A price only counts if it is the CURRENT price on the user's \
OWN regional store, in their currency — e.g. the Danish eShop in DKK. Do NOT pass off \
another country's price (or a converted "global" view) as theirs — many trackers \
default to that, so confirm the source is locked to the user's region. Region fallback: \
prefer the user's exact country; but if it isn't readable and you only find a far-off \
one (e.g. US prices for a European user), do NOT use that — try other nearby countries \
in their region instead (e.g. the UK or another European eShop for a Danish user) and \
clearly LABEL which country/currency the price is from as an approximation. A \
neighbouring-region price, labelled, is a fair proxy; a US price for a European user is \
not. You are free to find the right source: read the official store's deals page (use \
web_search with deep=true to load the real page, site= to focus a domain), or search \
the web for a reputable price tracker set to the right region — reason about whether \
it's region-correct. Use the same approach for any platform (Xbox, PlayStation, Switch, \
Steam); some stores' pages don't expose prices to search, so you may need a tracker or \
to report only the discount you can see. Always: name your source and say which region \
the price is from; suggest the user verify on the store; ignore subscriber-only prices \
("Game Pass"/"EA Play"); never quote grey-market key resellers (AllKeyShop, Eneba, \
gg.deals keys); a title you don't see on sale is NOT on sale; check sale-end dates \
against today; and if you can't confirm even a nearby-region live price, say so plainly \
— never guess a number.
- Recommend ONE primary new game with clear reasoning (why it fits mood, time, \
taste, and platform, plus a note on community reception and that it's not already \
in their library) and up to THREE alternatives with brief reasons.
- Turn structure: everything you write BEFORE your final message is shown to the user \
only transiently (a passing status line) and then discarded — so keep those working \
notes to a short phrase, and do NOT put your recommendation or any answer content \
there. Write your COMPLETE reply only in your FINAL message, once you have everything; \
never split the answer across earlier tool-calling turns. Don't open that reply with \
a horizontal rule (---) or divider.
- Follow-ups: within this conversation, remember what you've already suggested. \
On "I already played it" / "something else" / "shorter", exclude the prior pick \
and offer the next best WITHOUT re-asking everything you already know.
- Across sessions, call get_recent_recommendations to avoid repeating recent \
picks unless the user asks to revisit one.
- After you present a recommendation, call save_recommendation once to persist it.
- If a tool reports an error or returns nothing, adapt and be honest about what \
you couldn't verify; never invent ratings, platforms, or availability — confirm \
with web_search when unsure.
"""


def system_prompt_for_region(region: str | None, today: date | None = None) -> str:
    """Return the system prompt with the current date and the user's region injected.

    Both are appended as extra bullets so the base prompt stays a constant. The date
    lets the agent judge deal/sale freshness — store pages describe past, expired sales
    too, and the model has no innate sense of "today". The region lets it read the
    right regional store and currency directly instead of asking the user to confirm it.
    """
    extra = ""
    if today is not None:
        extra += (
            f"- Today's date is {today.isoformat()}. Deals and sales expire and web "
            f"results can be stale: before presenting a deal, check its end/validity "
            f"date against today and NEVER present a sale that has already ended as a "
            f"current offer. If you cannot confirm a deal is still live, say so rather "
            f"than stating it as a current price.\n"
        )
    if region:
        extra += (
            f"- The user is based in {region}. Treat {region} as their store region and "
            f"currency for prices, availability, and deals — use it directly (e.g. as the "
            f"region in a store deals search) and do NOT ask them to confirm it.\n"
        )
    return SYSTEM_PROMPT + extra


@dataclass
class AgentReply:
    """One agent turn for the caller (CLI/UI) to render."""

    message: str
    is_stateless_mode: bool = False
    """True when the memory store is unavailable, so personalization is limited."""

    tool_calls: list[str] = field(default_factory=list)
    """Names of tools invoked while producing this reply (for transparency)."""


@dataclass
class AgentEvent:
    """One streamed step of a turn (see :meth:`AgentRuntime.stream`).

    ``kind == "text"`` carries the model's FINAL answer in ``text`` (persist it);
    ``kind == "thinking"`` carries working notes from an intermediate tool-calling
    turn in ``text`` (show transiently, then discard — don't persist); ``kind ==
    "tool"`` names a tool the model is about to run in ``tool`` (show it transiently,
    e.g. "🔧 searching the web…").
    """

    kind: Literal["text", "tool", "thinking"]
    text: str = ""
    tool: str = ""


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
        answer: list[str] = []
        thinking: list[str] = []
        called: list[str] = []
        for event in self.stream(user_text):
            if event.kind == "text":
                answer.append(event.text)
            elif event.kind == "thinking":
                thinking.append(event.text)
            else:
                called.append(event.tool)
        # The final answer is what we keep; fall back to the working notes only if the
        # model produced no final message.
        return self._reply("\n\n".join(answer) or "\n\n".join(thinking), called)

    def stream(self, user_text: str) -> Iterator[AgentEvent]:
        """Run the tool loop, yielding thinking/answer text and tool-call events.

        Text from an intermediate turn (one that still calls tools) is the model's
        working notes and is yielded as ``kind="thinking"`` (shown transiently, not
        persisted); text from the FINAL turn is the answer, yielded as ``kind="text"``.
        The prompt steers the model to write its full answer only in that final turn.
        Raises ``BedrockServiceError`` (sanitized) if the model fails.
        """
        self._messages.append({"role": "user", "content": [{"text": user_text}]})

        for _ in range(_MAX_TOOL_ITERATIONS):
            result = self._bedrock.converse_tools(self._messages, self._tools.specs(), self._system)
            final = result.stop_reason != "tool_use" or not result.tool_uses
            if result.text.strip():
                yield AgentEvent(kind="text" if final else "thinking", text=result.text.strip())
            if result.assistant_content:
                self._messages.append({"role": "assistant", "content": result.assistant_content})

            if final:
                return

            tool_results: list[dict[str, Any]] = []
            for use in result.tool_uses:
                yield AgentEvent(kind="tool", tool=use.name)
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

        # Cap reached without a final answer — nudge the model to answer now instead of
        # leaving the turn as a pile of half-gathered working notes. The nudge is folded
        # into the last (user) toolResult message rather than appended as a second user
        # turn in a row, which Converse rejects (messages must alternate user/assistant).
        # Tools STAY in the request: once the history holds toolUse/toolResult blocks,
        # Converse requires toolConfig, so a bare (toolless) call fails validation with
        # "toolConfig field must be defined". The nudge — not the absence of tools — is
        # what steers it to a final text answer; if it still only calls a tool (which we
        # don't dispatch), result.text is empty and we fall back to the limit message.
        self._messages[-1]["content"].append({"text": _WRAP_UP_NUDGE})
        result = self._bedrock.converse_tools(self._messages, self._tools.specs(), self._system)
        if result.assistant_content:
            self._messages.append({"role": "assistant", "content": result.assistant_content})
        yield AgentEvent(kind="text", text=result.text.strip() or _ITERATION_LIMIT_MESSAGE)

    def _reply(self, message: str, called: list[str]) -> AgentReply:
        """Wrap ``message`` in an :class:`AgentReply`, reflecting memory health."""
        return AgentReply(
            message=message or _ITERATION_LIMIT_MESSAGE,
            is_stateless_mode=not self._memory.is_available,
            tool_calls=called,
        )
