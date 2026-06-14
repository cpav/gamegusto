"""Retro arcade machine theme for the Streamlit UI (Req 9.1, 9.2)."""

from __future__ import annotations

import streamlit as st

RETRO_ARCADE_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap');
:root {
    --arcade-bg: #0d0221;
    --arcade-neon-pink: #ff2e97;
    --arcade-neon-cyan: #2de2e6;
    --arcade-neon-yellow: #f9f871;
}
html, body, [class*="css"], .stMarkdown, .stButton button {
    font-family: 'Press Start 2P', monospace !important;
}
.stApp {
    background:
        repeating-linear-gradient(
            0deg, rgba(0,0,0,0.15) 0, rgba(0,0,0,0.15) 1px,
            transparent 1px, transparent 3px),
        radial-gradient(circle at center, #1a0540 0%, var(--arcade-bg) 100%);
    color: var(--arcade-neon-cyan);
}
h1, h2, h3 {
    color: var(--arcade-neon-pink);
    text-shadow: 0 0 6px var(--arcade-neon-pink), 0 0 12px var(--arcade-neon-pink);
}
.rec-card {
    border: 3px solid var(--arcade-neon-cyan);
    border-radius: 6px;
    box-shadow: 0 0 10px var(--arcade-neon-cyan), inset 0 0 12px rgba(45,226,230,0.25);
    padding: 1rem; background: rgba(13,2,33,0.85);
}
.stButton button {
    background: var(--arcade-neon-pink); color: #0d0221;
    border: 2px solid var(--arcade-neon-yellow); box-shadow: 0 4px 0 #b3005f;
}
/* Responsive: preserve theme on small screens (Req 9.2) */
@media (max-width: 640px) {
    html, body, [class*="css"] { font-size: 10px !important; }
    h1 { font-size: 1.1rem !important; }
    .rec-card { padding: 0.6rem; }
}
</style>
"""


def inject_retro_theme() -> None:
    """Inject the retro arcade CSS once per session (idempotent)."""
    if not st.session_state.get("_theme_injected"):
        st.markdown(RETRO_ARCADE_CSS, unsafe_allow_html=True)
        st.session_state["_theme_injected"] = True
