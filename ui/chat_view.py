"""Conversational chat view (Req 9.3).

Drives ``AgentRuntime.stream`` so the model's narration and tool use show as
*transient* status (a collapsing "thinking" panel) while the final
recommendation is rendered persistently in the retro playfield card. The LLM is
a hard dependency: a failure surfaces as a sanitized message (Req 10.1).
"""

from __future__ import annotations

import streamlit as st

from services.bedrock_service import BedrockServiceError
from ui.bootstrap import get_memory_service, get_runtime

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
    """Wrap an assistant ``message`` (markdown/text) in the retro rec-card div."""
    return f'<div class="rec-card">{message}</div>'


def _tool_label(name: str) -> str:
    """Return a friendly transient label for a tool name."""
    return _TOOL_LABELS.get(name, f"🔧 {name.replace('_', ' ')}")


def render_chat_view() -> None:
    """Render the chat history and handle a new turn with streamed status."""
    messages = st.session_state.setdefault("messages", [])
    if not messages:
        st.caption(
            "Tell me what you're in the mood to play — genre, vibe, how much time you've got."
        )

    for msg in messages:
        with st.chat_message(msg["role"]):
            if msg["role"] == "assistant":
                st.markdown(_card_html(msg["content"]), unsafe_allow_html=True)
            else:
                st.markdown(msg["content"])

    prompt = st.chat_input("Insert coin… what should I play?")
    if not prompt:
        return

    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        message = _stream_turn(prompt)
    if message:
        messages.append({"role": "assistant", "content": message})


def _stream_turn(prompt: str) -> str:
    """Stream one agent turn; show tool/narration transiently, return the answer."""
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
                card.markdown(_card_html("\n\n".join(parts)), unsafe_allow_html=True)
    except BedrockServiceError as exc:
        status.update(label="Out of order", state="error")
        st.error(str(exc))
        return ""

    message = "\n\n".join(parts)
    status.update(label="Ready", state="complete")
    card.markdown(_card_html(message), unsafe_allow_html=True)
    if not get_memory_service().is_available:
        st.caption("⚠️ memory unavailable — personalization is limited this session")
    return message
