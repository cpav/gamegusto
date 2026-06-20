"""Retro arcade × pinball machine theme for the Streamlit UI (Req 9.1, 9.2).

Blends an arcade-cabinet aesthetic (neon, CRT scanlines, pixel font) with
pinball-machine cues (chrome/metal trim, a glowing score-marquee header, bumper
buttons) and stays fully usable at phone widths.
"""

from __future__ import annotations

import streamlit as st

RETRO_ARCADE_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap');
@import url('https://fonts.googleapis.com/css2?family=VT323&display=swap');
:root {
    --arcade-bg: #0d0221;
    --arcade-bg-2: #1a0540;
    --arcade-neon-pink: #ff2e97;
    --arcade-neon-cyan: #2de2e6;
    --arcade-neon-yellow: #f9f871;
    --arcade-neon-green: #4cf26a;
    --pinball-chrome: linear-gradient(180deg, #f0f0f5 0%, #9aa0b5 45%, #4a4f66 55%, #c9cdda 100%);
}
html, body, [class*="css"], .stMarkdown, .stButton button, input, textarea, select {
    font-family: 'Press Start 2P', monospace !important;
}
/* Long-form body text uses the readable retro terminal face. */
.rec-card, .stChatMessage p, .lib-line, .hist-line { font-family: 'VT323', monospace !important; }
.stApp {
    background:
        repeating-linear-gradient(
            0deg, rgba(0,0,0,0.18) 0, rgba(0,0,0,0.18) 1px,
            transparent 1px, transparent 3px),
        radial-gradient(circle at 50% 0%, var(--arcade-bg-2) 0%, var(--arcade-bg) 70%);
    color: var(--arcade-neon-cyan);
}
h1, h2, h3 {
    color: var(--arcade-neon-pink);
    text-shadow: 0 0 6px var(--arcade-neon-pink), 0 0 12px var(--arcade-neon-pink);
    letter-spacing: 1px;
}
/* Pinball-style backglass marquee for the app title. */
.gg-marquee {
    text-align: center; padding: 0.7rem 1rem; margin: 0 0 1rem 0; border-radius: 10px;
    border: 3px solid transparent; border-image: var(--pinball-chrome) 1;
    background: linear-gradient(180deg, rgba(255,46,151,0.18), rgba(45,226,230,0.10)), #120630;
    box-shadow: 0 0 14px rgba(45,226,230,0.5), inset 0 0 18px rgba(255,46,151,0.25);
}
.gg-marquee .gg-title {
    color: var(--arcade-neon-yellow); font-size: 1.5rem;
    text-shadow: 0 0 8px var(--arcade-neon-yellow), 0 0 16px var(--arcade-neon-pink);
}
.gg-marquee .gg-sub { color: var(--arcade-neon-cyan); font-size: 0.6rem; opacity: 0.9; }
/* Recommendation "playfield" card. */
.rec-card {
    border: 3px solid var(--arcade-neon-cyan); border-radius: 8px;
    box-shadow: 0 0 12px var(--arcade-neon-cyan), inset 0 0 14px rgba(45,226,230,0.22);
    padding: 1rem 1.1rem; background: rgba(13,2,33,0.88); font-size: 1.25rem; line-height: 1.5;
}
.rec-card h1, .rec-card h2, .rec-card h3 { font-family: 'Press Start 2P', monospace !important; }
/* Bumper buttons. */
.stButton button {
    background: var(--arcade-neon-pink); color: #0d0221; border-radius: 8px;
    border: 2px solid var(--arcade-neon-yellow); box-shadow: 0 4px 0 #b3005f;
    transition: transform 0.05s ease;
}
.stButton button:hover { background: var(--arcade-neon-yellow); }
.stButton button:active { transform: translateY(3px); box-shadow: 0 1px 0 #b3005f; }
/* Score-panel styling for chat + inputs. */
.stChatMessage { border: 1px solid rgba(45,226,230,0.35); border-radius: 8px;
    background: rgba(18,6,48,0.6); }
.stChatInputContainer, [data-testid="stChatInput"] {
    border-top: 2px solid var(--arcade-neon-pink); }
.lib-line, .hist-line { font-size: 1.2rem; border-bottom: 1px dashed rgba(45,226,230,0.3);
    padding: 0.25rem 0; }
/* Responsive: keep everything operable on a phone (Req 9.2). */
@media (max-width: 640px) {
    html, body, [class*="css"] { font-size: 11px !important; }
    .gg-marquee .gg-title { font-size: 1.1rem; }
    .gg-marquee .gg-sub { font-size: 0.5rem; }
    .rec-card { padding: 0.7rem; font-size: 1.1rem; }
    .stButton button { width: 100%; }
    /* Taller, easier-to-tap chat input on small phones (e.g. iPhone SE). */
    [data-testid="stChatInput"] textarea {
        min-height: 3rem; font-size: 1rem; line-height: 1.4; }
}
</style>
"""

#: Pinball-backglass marquee header, rendered at the top of every view.
MARQUEE_HTML = (
    '<div class="gg-marquee">'
    '<div class="gg-title">🕹️ GAMEGUSTO 🎯</div>'
    '<div class="gg-sub">INSERT COIN · PICK YOUR NEXT GAME</div>'
    "</div>"
)


def inject_retro_theme() -> None:
    """Inject the retro arcade/pinball CSS once per session (idempotent)."""
    if not st.session_state.get("_theme_injected"):
        st.markdown(RETRO_ARCADE_CSS, unsafe_allow_html=True)
        st.session_state["_theme_injected"] = True


def render_marquee() -> None:
    """Render the pinball-backglass title marquee."""
    st.markdown(MARQUEE_HTML, unsafe_allow_html=True)
