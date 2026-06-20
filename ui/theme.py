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
/* Everything is retro: VT323 (a readable terminal face) is the base for all
   text, and Press Start 2P is used for headers, the marquee title, and buttons.
   No default (non-retro) fonts anywhere. */
html, body, [class*="css"], [class*="st-"], .stApp, .stMarkdown,
p, div, span, label, li, input, textarea, select, button {
    font-family: 'VT323', monospace !important;
}
html, body { font-size: 18px; }
h1, h2, h3, .gg-title, .stButton button {
    font-family: 'Press Start 2P', monospace !important;
}
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
.gg-marquee .gg-sub { color: var(--arcade-neon-cyan); font-size: 1.2rem; opacity: 0.95;
    text-shadow: 0 0 6px var(--arcade-neon-cyan); letter-spacing: 1px; }
/* Chat empty-state intro line, sized up to read like part of the title. */
.chat-intro { color: var(--arcade-neon-cyan); font-size: 1.6rem; text-align: center;
    margin: 0.4rem 0 1rem; text-shadow: 0 0 6px var(--arcade-neon-cyan); }
/* Recommendation "playfield" card. */
.rec-card {
    border: 3px solid var(--arcade-neon-cyan); border-radius: 8px;
    box-shadow: 0 0 12px var(--arcade-neon-cyan), inset 0 0 14px rgba(45,226,230,0.22);
    padding: 1rem 1.1rem; background: rgba(13,2,33,0.88); font-size: 1.3rem; line-height: 1.5;
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
/* Keep the pinned chat bar opaque so messages never bleed through it, and pad
   the content area so the last message always clears the bar. */
[data-testid="stBottom"], [data-testid="stBottomBlockContainer"] {
    background: var(--arcade-bg); }
[data-testid="stMainBlockContainer"], .block-container { padding-bottom: 7rem; }
.lib-line, .hist-line { font-size: 1.2rem; border-bottom: 1px dashed rgba(45,226,230,0.3);
    padding: 0.25rem 0; }
/* Responsive: keep everything readable and operable on a phone (Req 9.2). */
@media (max-width: 640px) {
    html, body { font-size: 13px !important; }
    .gg-marquee { padding: 0.5rem 0.6rem; }
    .gg-marquee .gg-title { font-size: 0.95rem; }
    .gg-marquee .gg-sub { font-size: 0.8rem; }
    .chat-intro { font-size: 1.05rem; }
    .rec-card { padding: 0.6rem; font-size: 1.05rem; }
    .lib-line, .hist-line { font-size: 1.05rem; }
    .stButton button { width: 100%; }
    [data-testid="stChatInput"] textarea {
        min-height: 2.75rem; font-size: 0.95rem; line-height: 1.35; }
    [data-testid="stMainBlockContainer"], .block-container { padding-bottom: 6rem; }
}
/* Small phones (e.g. iPhone SE, ~375px): a notch smaller again. */
@media (max-width: 400px) {
    html, body { font-size: 12px !important; }
    .gg-marquee .gg-title { font-size: 0.8rem; letter-spacing: 0; }
    .gg-marquee .gg-sub { font-size: 0.7rem; letter-spacing: 0; }
    .chat-intro { font-size: 0.95rem; }
    .rec-card { font-size: 1rem; padding: 0.55rem; }
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
    """Inject the retro arcade/pinball CSS.

    Streamlit rebuilds the DOM on every rerun, so the CSS must be re-emitted each
    run — guarding it to "once per session" makes the styling vanish when the view
    switches (chat ⇄ library) or any widget triggers a rerun.
    """
    st.markdown(RETRO_ARCADE_CSS, unsafe_allow_html=True)


def render_marquee() -> None:
    """Render the pinball-backglass title marquee."""
    st.markdown(MARQUEE_HTML, unsafe_allow_html=True)
