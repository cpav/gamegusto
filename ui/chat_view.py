"""Conversational chat view (Req 9.3).

Layout blueprint: Streamlit's native chat pattern — ``st.chat_input`` at the top
level stays pinned to the bottom of the viewport on every device (so it's always
reachable on a laptop or an iPhone SE without scrolling), and the messages flow
above it. A trailing spacer plus an opaque input bar keep the last reply from
sliding under the input. Messages read as a conversation: the user on the right,
the agent on the left, each in a retro speech bubble. The agent's reply reveals
word-by-word; tool use shows transiently; quick follow-up chips and auto-scroll
round out the feel.

The LLM is a hard dependency: a failure surfaces as a sanitized message (Req 10.1).
"""

from __future__ import annotations

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
    _scroll_to_bottom()


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


def _stream_turn(prompt: str) -> str:
    """Stream one agent turn: tool use transiently, then reveal the reply word-by-word."""
    runtime = get_runtime()
    status = st.status("Consulting the arcade oracle…", expanded=False)
    card = st.empty()
    parts: list[str] = []
    try:
        for event in runtime.stream(prompt):
            if event.kind == "tool":
                status.update(label=_tool_label(event.tool))
                status.write(_tool_label(event.tool))
            elif event.text:
                parts.append(event.text)
    except BedrockServiceError as exc:
        status.update(label="Out of order", state="error")
        st.error(str(exc))
        return ""

    message = "\n\n".join(parts)
    status.update(label="Ready", state="complete")
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


def _scroll_to_bottom() -> None:
    """Auto-scroll the page to the latest message (best-effort)."""
    components.html(
        "<script>const d=window.parent.document;"
        "const m=d.querySelectorAll('[data-testid=\"stChatMessage\"]');"
        "if(m.length){m[m.length-1].scrollIntoView({behavior:'smooth',block:'end'});}</script>",
        height=0,
    )
