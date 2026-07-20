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

import json
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from agent.tools import ToolRegistry
from services.bedrock_service import BedrockService, BedrockServiceError, ConverseResult
from services.memory_service import MemoryService

#: Raised if a streaming round ends without its final assembled result — a
#: broken-stream condition the service normally surfaces itself.
_STREAM_ENDED_EARLY_MESSAGE = "The recommendation service was interrupted — please try again."

#: Hard cap on tool-call rounds per user turn, so a misbehaving loop terminates.
_MAX_TOOL_ITERATIONS = 8

#: Cap on retained conversation turns (one turn = a user message plus everything up
#: to the next user message). Each Bedrock round re-sends the whole history, so an
#: unbounded session grows cost quadratically; older turns are dropped whole — never
#: splitting a toolUse from its toolResult — so the window is always Converse-valid.
_MAX_HISTORY_TURNS = 8

#: A completed turn's tool result larger than this (JSON chars) is replaced by
#: ``_ELIDED_NOTE`` at the next turn's start (see ``_compact_history``). Small
#: results (a platform list, a save confirmation) are kept as cheap context.
_ELIDE_THRESHOLD_CHARS = 600

#: What a stale, bulky tool result is replaced with. Tells the model the data was
#: removed — not empty — so it re-fetches instead of concluding e.g. "no results".
_ELIDED_NOTE = (
    "[stale tool output from an earlier turn was removed to save context; "
    "call the tool again if you need this data]"
)

#: Extra "closing" rounds allowed after the cap, so the model can finish a cheap pending
#: action (e.g. save_recommendation) and THEN write its answer, instead of stopping at a
#: bare "let me put it together" with no recommendation.
_MAX_WRAPUP_ROUNDS = 2

#: Fallback shown if the model never settles on a final answer within the cap.
_ITERATION_LIMIT_MESSAGE = (
    "I'm having trouble pulling this together right now — could you rephrase or "
    "give me a bit more to go on?"
)

#: Sent once the tool-round cap is hit, to force a final answer (no more tools) rather
#: than leaving the turn as half-gathered working notes.
_WRAP_UP_NUDGE = (
    "You've gathered enough — do NOT search any further. If you still need to save the "
    "recommendation, do it now; then write your COMPLETE recommendation to the user in "
    "your usual rich format: a short title line with an emoji, the pick with clear "
    'reasoning, and a few alternatives. Do not reply with only a status like "let me '
    'put it together" — write the actual recommendation.'
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
never split the answer across earlier tool-calling turns. START that final reply \
directly with the recommendation (e.g. a title line) — do NOT begin it by narrating \
what you are about to do ("I now have enough…", "Let me now cross-reference your \
library…", "Let me save this…"); that planning belongs in the transient notes, not the \
answer. Don't open the reply with a horizontal rule (---) or divider either.
- Follow-ups: within this conversation, remember what you've already suggested. \
On "I already played it" / "something else" / "shorter", exclude the prior pick \
and offer the next best WITHOUT re-asking everything you already know.
- Across sessions, call get_recent_recommendations to avoid repeating recent \
picks unless the user asks to revisit one. It also carries the user's feedback on \
past picks ("loved" / "not_for_me") — treat that as a strong taste signal: lean \
toward what loved picks have in common and away from what rejected ones share.
- After you present a recommendation, call save_recommendation once to persist it.
- If a tool reports an error or returns nothing, adapt and be honest about what \
you couldn't verify; never invent ratings, platforms, or availability — confirm \
with web_search when unsure.
"""


#: Minimum length for save-turn text to count as the presented answer. A real
#: recommendation runs several paragraphs; save narration ("let me save this") is a
#: sentence. The threshold keeps narration transient without risking a real answer.
_MIN_PRESENTED_ANSWER_CHARS = 200


def _presents_answer(result: Any) -> bool:
    """True when a tool-calling turn's text is the answer, not working notes.

    The prompt tells the model to present the recommendation and then call
    ``save_recommendation`` — it often does both in ONE turn. Substantial text in a
    turn whose only tool calls are saves is therefore the recommendation being
    presented; classifying it as transient "thinking" (as for research turns) would
    discard the actual answer and keep only the model's short closing follow-up.
    """
    if not result.tool_uses or any(u.name != "save_recommendation" for u in result.tool_uses):
        return False
    return len(result.text.strip()) >= _MIN_PRESENTED_ANSWER_CHARS


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
    e.g. "🔧 searching the web…"); ``kind == "delta"`` (only when streaming with
    ``deltas=True``) carries one token-level text fragment of the round in
    progress — render it live, then let the round's closing ``thinking``/``text``
    event replace the accumulated fragments (deltas alone are never authoritative).
    """

    kind: Literal["text", "tool", "thinking", "delta"]
    text: str = ""
    tool: str = ""


class AgentRuntime:
    """Drives the Bedrock tool-use loop for one user's conversation."""

    def __init__(
        self,
        bedrock: BedrockService,
        tools: ToolRegistry,
        memory: MemoryService,
        system_prompt: str | Callable[[], str] = SYSTEM_PROMPT,
    ) -> None:
        """Build the runtime around the model, the tool registry, and memory.

        ``system_prompt`` may be a plain string or a zero-arg callable returning one.
        A callable is resolved fresh at the start of every turn — the prompt embeds
        "today's date" for deal-freshness checks, and a Streamlit session (which caches
        this runtime) can live past midnight, so baking the date in at build time
        would quietly go stale.
        """
        self._bedrock = bedrock
        self._tools = tools
        self._memory = memory
        self._system = system_prompt
        self._messages: list[dict[str, Any]] = []
        #: Converse ``usage`` counters summed over the LAST turn's rounds (keys like
        #: inputTokens / outputTokens / cacheReadInputTokens / cacheWriteInputTokens).
        #: The UI reads this after a turn to show what the turn actually cost.
        self.last_turn_usage: dict[str, int] = {}

    def system_prompt(self) -> str:
        """Return the current system prompt (resolving a callable prompt source)."""
        return self._system() if callable(self._system) else self._system

    def reset(self) -> None:
        """Clear the conversation history to start a fresh session."""
        self._messages = []

    def seed_transcript(self, transcript: list[dict[str, Any]]) -> None:
        """Restore model-side context from a persisted UI transcript.

        Used after a page refresh: the UI transcript (role/content text pairs) is
        replayed as plain text messages so the agent keeps conversational
        continuity without the original toolUse/toolResult blocks (which are not
        persisted). Consecutive same-role entries are merged and the history must
        open with a user message, keeping the sequence Converse-valid. No-op when
        a conversation is already in progress.
        """
        if self._messages:
            return
        merged: list[dict[str, Any]] = []
        for entry in transcript:
            role = entry.get("role")
            text = str(entry.get("content", "")).strip()
            if role not in ("user", "assistant") or not text:
                continue
            if not merged and role != "user":
                continue  # Converse requires the history to open with a user message
            if merged and merged[-1]["role"] == role:
                merged[-1]["content"][0]["text"] += f"\n\n{text}"
            else:
                merged.append({"role": role, "content": [{"text": text}]})
        if merged and merged[-1]["role"] == "user":
            merged.pop()  # an unanswered user message would break role alternation
        self._messages = merged
        self._trim_history()

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
            elif event.kind == "tool":
                called.append(event.tool)
        # The final answer is what we keep; fall back to the working notes only if the
        # model produced no final message.
        return self._reply("\n\n".join(answer) or "\n\n".join(thinking), called)

    def stream(self, user_text: str, *, deltas: bool = False) -> Iterator[AgentEvent]:
        """Run the tool loop, yielding thinking/answer text and tool-call events.

        Text from an intermediate turn (one that still calls tools) is the model's
        working notes and is yielded as ``kind="thinking"`` (shown transiently, not
        persisted); text from the FINAL turn is the answer, yielded as ``kind="text"``.
        The prompt steers the model to write its full answer only in that final turn.
        With ``deltas=True`` each round's text additionally streams token-by-token
        as ``kind="delta"`` events ahead of that round's closing event (callers that
        don't opt in keep the exact event sequence they always had). Raises
        ``BedrockServiceError`` (sanitized) if the model fails.
        """
        self._compact_history()
        self._trim_history()
        checkpoint = len(self._messages)
        self._messages.append({"role": "user", "content": [{"text": user_text}]})
        self.last_turn_usage = {}
        try:
            yield from self._turn(deltas)
        except BaseException:
            # A failed (or abandoned mid-run) turn must not leave partial state in the
            # history — an orphaned user message or a toolUse without its toolResult
            # makes Converse reject the NEXT call ("roles must alternate" / "toolConfig
            # must be defined"), so one transient failure would brick every subsequent
            # turn. Roll back to the pre-turn state; a retry then starts clean.
            del self._messages[checkpoint:]
            raise

    def _compact_history(self) -> None:
        """Shrink completed turns' bulky tool results down to a stub.

        Raw tool payloads (a library dump is ~5K tokens, a deep web search up to
        ~5K) stay in the history and are RE-SENT on every round of every later
        turn — measured on a real session, ~90% of a ~100K-token request was stale
        tool output whose conclusions were already in the assistant's answers. The
        toolResult *blocks* must remain (Converse pairs them with the preceding
        toolUse ids), so only their content is replaced. Run at turn start, when
        the prior turn is complete and the 5-minute prompt cache has typically
        expired anyway — so the rewrite costs nothing extra in cache churn. Small
        results are kept: eliding them saves little and context is context.
        """
        for message in self._messages:
            if message["role"] != "user":
                continue
            for block in message["content"]:
                result = block.get("toolResult")
                if not result:
                    continue
                content = result.get("content")
                if content and len(json.dumps(content)) > _ELIDE_THRESHOLD_CHARS:
                    result["content"] = [{"text": _ELIDED_NOTE}]

    def _trim_history(self) -> None:
        """Drop the oldest whole turns so at most ``_MAX_HISTORY_TURNS`` (including
        the turn about to start) remain. A turn starts at a user message carrying no
        toolResult blocks, so the cut never separates a toolUse from its result and
        the window always opens on a plain user message, as Converse requires."""
        starts = [
            index
            for index, message in enumerate(self._messages)
            if message["role"] == "user"
            and not any("toolResult" in block for block in message["content"])
        ]
        excess = len(starts) - (_MAX_HISTORY_TURNS - 1)
        if excess > 0:
            del self._messages[: starts[excess]]

    def _add_usage(self, result: Any) -> None:
        """Fold one round's Converse ``usage`` counters into the turn total."""
        for key, value in getattr(result, "usage", {}).items():
            if isinstance(value, int):
                self.last_turn_usage[key] = self.last_turn_usage.get(key, 0) + value

    def _converse_round(
        self, system: str, deltas: bool
    ) -> Generator[AgentEvent, None, ConverseResult]:
        """Run one model round, yielding delta events when streaming is on.

        Returns the round's :class:`ConverseResult` either way (callers take it
        via ``yield from``), so the loop logic is identical for both modes.
        """
        if not deltas:
            return self._bedrock.converse_tools(self._messages, self._tools.specs(), system)
        result: ConverseResult | None = None
        for item in self._bedrock.converse_tools_stream(
            self._messages, self._tools.specs(), system
        ):
            if isinstance(item, str):
                yield AgentEvent(kind="delta", text=item)
            else:
                result = item
        if result is None:  # defensive: the stream contract ends with a result
            raise BedrockServiceError(_STREAM_ENDED_EARLY_MESSAGE)
        return result

    def _turn(self, deltas: bool = False) -> Iterator[AgentEvent]:
        """Run one turn's tool loop over the already-appended user message."""
        system = self.system_prompt()  # resolved once per turn (keeps the date fresh)
        for _ in range(_MAX_TOOL_ITERATIONS):
            result = yield from self._converse_round(system, deltas)
            self._add_usage(result)
            final = result.stop_reason != "tool_use" or not result.tool_uses
            if result.text.strip():
                keep = final or _presents_answer(result)
                yield AgentEvent(kind="text" if keep else "thinking", text=result.text.strip())
            if result.assistant_content:
                self._messages.append({"role": "assistant", "content": result.assistant_content})

            if final:
                return
            yield from self._apply_tools(result)

        # Cap reached without a final answer — nudge the model to close out now instead of
        # leaving the turn as a pile of half-gathered working notes. The nudge is folded
        # into the last (user) toolResult message rather than appended as a second user
        # turn in a row, which Converse rejects (messages must alternate user/assistant).
        # Tools STAY in the request: once the history holds toolUse/toolResult blocks,
        # Converse requires toolConfig, so a bare (toolless) call fails validation with
        # "toolConfig field must be defined". We give it a few CLOSING rounds: if it still
        # calls a tool (e.g. save_recommendation), we dispatch that and let it answer next,
        # rather than mistaking its "let me save…" narration for the final recommendation.
        self._messages[-1]["content"].append({"text": _WRAP_UP_NUDGE})
        for _ in range(_MAX_WRAPUP_ROUNDS):
            result = yield from self._converse_round(system, deltas)
            self._add_usage(result)
            final = result.stop_reason != "tool_use" or not result.tool_uses
            if result.assistant_content:
                self._messages.append({"role": "assistant", "content": result.assistant_content})
            if final:
                yield AgentEvent(kind="text", text=result.text.strip() or _ITERATION_LIMIT_MESSAGE)
                return
            if result.text.strip():
                # Substantial text alongside a pure save is the answer being presented;
                # anything else is closing narration — shown transiently, not kept.
                keep = _presents_answer(result)
                yield AgentEvent(kind="text" if keep else "thinking", text=result.text.strip())
            yield from self._apply_tools(result)
        yield AgentEvent(kind="text", text=_ITERATION_LIMIT_MESSAGE)

    def _apply_tools(self, result: Any) -> Iterator[AgentEvent]:
        """Dispatch every tool the model asked for, yielding a tool event per call and
        appending the collected results as the next user turn."""
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

    def _reply(self, message: str, called: list[str]) -> AgentReply:
        """Wrap ``message`` in an :class:`AgentReply`, reflecting memory health."""
        return AgentReply(
            message=message or _ITERATION_LIMIT_MESSAGE,
            is_stateless_mode=not self._memory.is_available,
            tool_calls=called,
        )
