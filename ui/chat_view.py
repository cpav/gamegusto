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

import random
import time

import streamlit as st

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
#: A fresh handful is sampled per session to keep the empty state lively.
_STARTER_PROMPTS = {
    "🕹️ Short & snappy": "Something short and arcadey I can finish in one sitting",
    "🐉 Big RPG": "A fantasy RPG I can really sink into",
    "👾 Catch 'em all": "A monster-taming / creature-collecting game",
    "♟️ Something tactical": "A tactical or strategy game that makes me think",
    "💸 On sale now": "What's on a good deal right now for my platforms?",
    "🤔 Help me choose": (
        "I've got a few directions in mind — help me pick, deals welcome as a tiebreaker"
    ),
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
    "find_deals": "💸 checking store deals",
    "get_recent_recommendations": "🧠 recalling recent picks",
    "save_recommendation": "💾 saving this pick",
}


def _card_html(message: str) -> str:
    """Wrap an assistant ``message`` (markdown/text) in the retro rec-card bubble."""
    return f'<div class="rec-card">{message}</div>'


def _user_html(message: str) -> str:
    """Wrap a user ``message`` in the right-aligned speech bubble."""
    return f'<div class="user-bubble">{message}</div>'


def _tool_label(name: str) -> str:
    """Return a friendly transient label for a tool name."""
    return _TOOL_LABELS.get(name, f"🔧 {name.replace('_', ' ')}")


def render_chat_view() -> None:
    """Render the conversation, handle a new turn, follow-up chips, and auto-scroll."""
    history = st.session_state.setdefault("messages", [])
    if not history:
        st.markdown(
            '<div class="chat-intro">Tell me what you\'re in the mood to play — '
            "genre, vibe, how much time you've got.</div>",
            unsafe_allow_html=True,
        )
        _render_starters()
    for msg in history:
        _render_message(msg["role"], msg["content"])

    typed = st.chat_input("Insert coin… what should I play?")  # pinned to viewport bottom
    prompt = typed or st.session_state.pop("_pending_prompt", None)
    if prompt:
        history.append({"role": "user", "content": prompt})
        _render_message("user", prompt)
        with st.chat_message("assistant", avatar=_AVATARS["assistant"]):
            message = _stream_turn(prompt)
        if message:
            history.append({"role": "assistant", "content": message})

    if any(m["role"] == "assistant" for m in history):
        _render_chips()
    # In-flow spacer so the last message always clears the pinned input bar.
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


def _render_starters() -> None:
    """Flash a few conversation-starter chips on the empty screen.

    A fresh sample is drawn once per session (stored in session state so it stays
    stable across reruns), then laid out two-per-row to read well on a phone. A
    click queues that starter's text as the first turn.
    """
    starters = st.session_state.get("_starters")
    if starters is None:
        k = min(_STARTER_COUNT, len(_STARTER_PROMPTS))
        starters = random.sample(list(_STARTER_PROMPTS), k=k)
        st.session_state["_starters"] = starters
    for row in range(0, len(starters), 2):
        pair = starters[row : row + 2]
        cols = st.columns(len(pair))
        for col, label in zip(cols, pair):
            if col.button(label, key=f"starter_{label}", use_container_width=True):
                st.session_state["_pending_prompt"] = _STARTER_PROMPTS[label]
                st.rerun()


def _stream_turn(prompt: str) -> str:
    """Stream one agent turn: a transient "thinking" line, then the reply word-by-word.

    The thinking line is a single placeholder that updates per tool and is cleared
    when done — no persistent status box and no leftover "Ready"/tool text.
    """
    runtime = get_runtime()
    thinking = st.empty()
    card = st.empty()
    thinking.markdown('<div class="gg-thinking">🕹️ thinking…</div>', unsafe_allow_html=True)
    parts: list[str] = []
    try:
        for event in runtime.stream(prompt):
            if event.kind == "tool":
                thinking.markdown(
                    f'<div class="gg-thinking">{_tool_label(event.tool)}…</div>',
                    unsafe_allow_html=True,
                )
            elif event.text:
                parts.append(event.text)
    except BedrockServiceError as exc:
        thinking.empty()
        st.error(str(exc))
        return ""

    thinking.empty()
    message = "\n\n".join(parts)
    _typewriter(card, message)
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
