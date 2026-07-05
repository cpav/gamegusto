"""Conversational chat view (Req 9.3).

Layout blueprint: Streamlit's native chat pattern — ``st.chat_input`` at the top
level stays pinned to the bottom of the viewport on every device (so it's always
reachable on a laptop or an iPhone SE without scrolling), and the messages flow
above it. A trailing spacer plus an opaque input bar keep the last reply from
sliding under the input. Messages read as a conversation: the user on the right,
the agent on the left, each in a retro speech bubble. The agent's reply reveals
word-by-word; tool use shows transiently; quick follow-up chips round out the feel.

The LLM is a hard dependency: a failure surfaces as a sanitized message (Req 10.1).
"""

from __future__ import annotations

import re
import time

import streamlit as st
import streamlit.components.v1 as components

from services.bedrock_service import BedrockServiceError
from ui.bootstrap import get_memory_service, get_runtime

#: Space-invaders avatars: the agent is the invader, the user a fellow alien.
_AVATARS = {"assistant": "👾", "user": "👽"}

#: One-tap follow-ups (short labels for phones) -> the text actually sent.
_CHIP_PROMPTS = {
    "⏱️ Shorter": "Something shorter",
    "✅ Played it": "I already played that one",
    "🎲 Surprise me": "Surprise me with something different",
}

#: Conversation starters (short labels -> the text sent) shown on the empty screen.
#: Kept genre-agnostic — about *ways in*, not specific tastes — and a fresh handful
#: is sampled per session to keep the empty state lively.
_STARTER_PROMPTS = {
    "🤔 Help me decide": "I'm not sure what I feel like — help me figure out what to play next",
    "🎯 Match my taste": "Recommend something new based on the taste in my library",
    "💸 Good deals": "What good games are on a deal right now for my platforms?",
    "⏱️ Short on time": "I've only got a short while tonight — what should I play?",
    "🆕 Surprise me": "Surprise me with a new game I might love",
    "👀 Hidden gem": "Suggest a great game I've probably overlooked",
}

#: How many starters to flash at once (a 2x2 grid reads well on phones).
_STARTER_COUNT = 4

#: Total seconds the word-by-word reveal is allowed to take (kept snappy).
_TYPE_BUDGET = 1.2

#: Friendly label per tool, shown transiently while the agent works.
_TOOL_LABELS = {
    "get_owned_platforms": "🎮 checking your platforms",
    "add_platform": "🎮 adding a platform",
    "remove_platform": "🎮 removing a platform",
    "get_library": "📚 reading your library",
    "add_manual_game": "📚 adding a game",
    "set_game_fields": "📚 updating a game",
    "import_gmail": "📧 importing purchases",
    "enrich_game": "🔎 looking up game details",
    "web_search": "🌐 searching the web",
    "get_recent_recommendations": "🧠 recalling recent picks",
    "save_recommendation": "💾 saving this pick",
}


def _card_html(message: str) -> str:
    """Wrap an assistant ``message`` (markdown) in the retro rec-card bubble.

    The blank lines around ``message`` are load-bearing: Streamlit renders this via
    markdown, and a raw ``<div>`` opens a CommonMark HTML block that swallows every
    following line up to the next blank line as literal text — so a reply whose first
    paragraph held markdown (``**bold**``, ``~~strike~~``, ``- bullets``) showed those
    characters raw. Separating the div from the content with blank lines makes the WHOLE
    message parse as markdown, not just the part after its first blank line."""
    return f'<div class="rec-card">\n\n{message}\n\n</div>'


def _user_html(message: str) -> str:
    """Wrap a user ``message`` in the right-aligned speech bubble."""
    return f'<div class="user-bubble">{message}</div>'


def _tool_label(name: str) -> str:
    """Return a friendly transient label for a tool name."""
    return _TOOL_LABELS.get(name, f"🔧 {name.replace('_', ' ')}")


def _collapse_ws(text: str) -> str:
    """Collapse runs of whitespace/newlines to single spaces for the transient note.

    The full note is kept (not truncated) — CSS wraps it over as many lines as needed,
    so the user reads the model's whole working thought, not a cut-off fragment.
    """
    return " ".join(text.split())


def _strip_leading_rule(text: str) -> str:
    """Drop leading blank lines and markdown horizontal rules (---, ***, ___) the model
    sometimes prefixes, which render as an ugly bar above the reply."""
    lines = text.split("\n")
    while lines and (not lines[0].strip() or re.fullmatch(r"\s*([-*_])(?:\s*\1){2,}\s*", lines[0])):
        lines.pop(0)
    return "\n".join(lines)


#: Conversational lead-ins peeled before testing an opener ("Good — let me pull up…").
_NARRATION_LEAD_INS = ("okay", "ok", "alright", "great", "good", "perfect", "excellent")

#: Openers that mark a leading paragraph as process/tool narration ("let me now
#: cross-reference…", "I now have enough…") rather than the recommendation itself.
#: Deliberately tied to gathering verbs — never "let me recommend/suggest/tell" — so a
#: real answer is never mistaken for narration and stripped.
_NARRATION_OPENERS = (
    "i now have",
    "i have everything",
    "i have enough",
    "i've got everything",
    "now let me",
    "let me now",
    "let me check",
    "let me search",
    "let me look",
    "let me pull",
    "let me verify",
    "let me confirm",
    "let me save",
    "let me cross",
    "let me compile",
    "let me gather",
    "let me quickly",
    "let me also",
    "let me start by",
    "let me dig",
    "let me assemble",
    "let me review",
    "let me put together",
)


def _is_process_narration(paragraph: str) -> bool:
    """True if ``paragraph`` opens by narrating the gathering process, not the answer."""
    head = paragraph.strip().lower()
    for lead in _NARRATION_LEAD_INS:  # peel one interjection so "Good — let me check…" matches
        if head.startswith(lead):
            head = head[len(lead) :].lstrip(" ,.:!—–-").strip()
            break
    return head.startswith(_NARRATION_OPENERS)


def _strip_leading_narration(text: str) -> str:
    """Drop leading paragraphs that narrate the *process* instead of the answer.

    On its final turn the model sometimes prefixes the recommendation with a line like
    "I now have a solid picture — let me cross-reference your library…". That planning
    belongs in the transient notes, not the kept reply. Only leading paragraphs that
    *open* with a known gathering phrase are removed, and never the last paragraph, so a
    genuine intro ("Based on your library, your taste is clear…") and an all-narration
    reply are both left intact.
    """
    paras = re.split(r"\n\s*\n", text)
    i = 0
    while i < len(paras) - 1 and _is_process_narration(paras[i]):
        i += 1
    return "\n\n".join(paras[i:]) if i else text


def _clean_reply(text: str) -> str:
    """Trim a reply's leading process-narration and any stray leading horizontal rule."""
    return _strip_leading_rule(_strip_leading_narration(_strip_leading_rule(text)))


def render_chat_view() -> None:
    """Render the conversation, handle a new turn, follow-up chips, and auto-scroll.

    A turn is split across two runs so the agent's blocking stream never paints over
    a stale frame: submitting only appends the user message and reruns; the *next*
    run renders the whole history (now including that message) and then streams the
    answer into a freshly-added assistant block. Because the pre-stream frame has no
    reply at that position, Streamlit has no prior answer to show ghosted under the
    "thinking…" line while the model works.
    """
    history = st.session_state.setdefault("messages", [])

    # The intro + starters live in ONE persistent placeholder so leaving the empty
    # state actively *clears* them (an emptied node) rather than leaving the starter
    # buttons behind as a ghosted "stale" layer during the next, blocking run. The
    # slot occupies the same position every run, so .empty() reliably replaces it.
    intro_slot = st.empty()
    awaiting = st.session_state.get("_pending_prompt") or st.session_state.get("_pending_answer")
    if not history and not awaiting:
        with intro_slot.container():
            st.markdown(
                '<div class="chat-intro">Tell me what you\'re in the mood to play — '
                "genre, vibe, how much time you've got.</div>",
                unsafe_allow_html=True,
            )
            _render_starters()
        _pin_to_top()
    else:
        intro_slot.empty()

    for msg in history:
        _render_message(msg["role"], msg["content"])

    # An answer queued by the previous run: stream it now, below the rendered history.
    pending = st.session_state.pop("_pending_answer", None)
    if pending is not None:
        with st.chat_message("assistant", avatar=_AVATARS["assistant"]):
            message = _stream_turn(pending)
        if message:
            history.append({"role": "assistant", "content": message})

    typed = st.chat_input("Insert coin… what should I play?")  # pinned to viewport bottom
    prompt = typed or st.session_state.pop("_pending_prompt", None)
    if prompt:
        # Record the turn and hand off to the next run to stream the reply cleanly.
        history.append({"role": "user", "content": prompt})
        st.session_state["_pending_answer"] = prompt
        st.rerun()

    if any(m["role"] == "assistant" for m in history):
        _render_chips()
    # In-flow spacer so the last message clears the pinned input bar — only once
    # there's a conversation; on the empty state it would just add height that
    # overflows the viewport and pushes the marquee out of view.
    if history:
        st.markdown('<div class="gg-spacer"></div>', unsafe_allow_html=True)


def _render_message(role: str, content: str) -> None:
    """Render one message as a speech bubble (agent left, user right)."""
    with st.chat_message(role, avatar=_AVATARS.get(role)):
        html = _card_html(content) if role == "assistant" else _user_html(content)
        st.markdown(html, unsafe_allow_html=True)


def _render_chips() -> None:
    """Render one-tap follow-up chips; a click queues that text as the next turn."""
    chips = list(_CHIP_PROMPTS)
    cols = st.columns(len(chips))
    for col, chip in zip(cols, chips):
        if col.button(chip, key=f"chip_{chip}", use_container_width=True):
            st.session_state["_pending_prompt"] = _CHIP_PROMPTS[chip]
            st.rerun()


def _pin_to_top() -> None:
    """Keep the empty state scrolled to the top so the marquee/intro stay visible.

    Streamlit wraps a chat app in an auto-scroll-to-bottom container (it exists
    because of ``st.chat_input``); once the starter chips make the empty state taller
    than a laptop/desktop viewport, that container scrolls past the GameGusto marquee
    on open. A one-shot scroll loses the race — an async rerun (e.g. the timezone
    detection resolving) re-fires Streamlit's scroll-to-bottom afterwards — so we hold
    the top for ~2s, then stop. We also stop the instant the user scrolls, so it never
    fights intentional scrolling, and it only runs in the empty state (never during a
    real conversation, where scroll-to-latest is wanted). The 0-height iframe reaches
    the parent document because its srcdoc shares the app's origin (allow-same-origin).
    """
    components.html(
        """
        <script>
          const doc = window.parent.document;
          const sel = '[data-testid="stAppScrollToBottomContainer"]';
          const top = () => { const c = doc.querySelector(sel); if (c) c.scrollTop = 0; };
          top();
          let live = true;
          const id = setInterval(() => { if (live) top(); }, 100);
          const stop = () => { live = false; clearInterval(id); };
          const c = doc.querySelector(sel);
          if (c) {
            c.addEventListener('wheel', stop, {once: true, passive: true});
            c.addEventListener('touchmove', stop, {once: true, passive: true});
          }
          setTimeout(stop, 2000);
        </script>
        """,
        height=0,
    )


def _render_starters() -> None:
    """Show a fixed set of conversation-starter chips on the empty screen.

    A fixed (not random) handful so they don't change on every reload, laid out
    two-per-row to read well on a phone. A click queues that starter as the first turn.
    """
    starters = list(_STARTER_PROMPTS)[:_STARTER_COUNT]
    for row in range(0, len(starters), 2):
        pair = starters[row : row + 2]
        cols = st.columns(len(pair))
        for col, label in zip(cols, pair):
            if col.button(label, key=f"starter_{label}", use_container_width=True):
                st.session_state["_pending_prompt"] = _STARTER_PROMPTS[label]
                st.rerun()


def _stream_turn(prompt: str) -> str:
    """Stream one agent turn: a transient "thinking" line, then the reply word-by-word.

    A SINGLE placeholder carries the turn: it shows the "thinking"/tool line and is
    then overwritten in place by the reply. Using one slot (not a separate empty
    card) matters — an empty placeholder left below the thinking line reuses the
    previous turn's reply at that delta path and shows it ghosted until written, so
    the prior answer would flash under "thinking…". Writing the slot immediately
    avoids that.
    """
    runtime = get_runtime()
    slot = st.empty()
    slot.markdown('<div class="gg-thinking">🕹️ thinking…</div>', unsafe_allow_html=True)
    answer: list[str] = []
    thinking: list[str] = []
    note = ""  # the model's latest working note, kept visible while it acts on it
    try:
        for event in runtime.stream(prompt):
            if event.kind == "thinking":
                # Show the model's actual working note (its words), in full, not a label.
                note = _collapse_ws(event.text)
                thinking.append(event.text)
                slot.markdown(f'<div class="gg-thinking">💭 {note}</div>', unsafe_allow_html=True)
            elif event.kind == "tool":
                # Keep the latest note as the headline (it says what the model is doing);
                # only fall back to the tool label if it called a tool without narrating.
                line = f"💭 {note}" if note else f"{_tool_label(event.tool)}…"
                slot.markdown(f'<div class="gg-thinking">{line}</div>', unsafe_allow_html=True)
            elif event.text:  # final answer — this is what we keep
                answer.append(event.text)
    except BedrockServiceError as exc:
        slot.empty()
        st.error(str(exc))
        return ""

    # Persist only the final answer; fall back to the notes if there was no final text.
    message = _clean_reply("\n\n".join(answer) or "\n\n".join(thinking))
    if message:
        _typewriter(slot, message)
    else:
        slot.empty()  # no text this turn — don't leave the "thinking…" line behind
    if not get_memory_service().is_available:
        st.caption("⚠️ memory unavailable — personalization is limited this session")
    return message


def _typewriter(placeholder: object, text: str) -> None:
    """Reveal ``text`` word-by-word into ``placeholder``, capped to a snappy budget."""
    words = text.split(" ")
    if not words:
        return
    delay = min(0.025, _TYPE_BUDGET / len(words))
    shown: list[str] = []
    for word in words:
        shown.append(word)
        placeholder.markdown(_card_html(" ".join(shown)), unsafe_allow_html=True)  # type: ignore[attr-defined]
        time.sleep(delay)
    placeholder.markdown(_card_html(text), unsafe_allow_html=True)  # type: ignore[attr-defined]
