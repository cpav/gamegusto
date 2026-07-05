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
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&display=swap');
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
/* Streamlit renders its icons as Material Symbols ligature text (e.g. the sidebar
   toggle is the literal string "keyboard_double_arrow_right" in an icon font);
   the global font override above must not catch those spans or the ligature
   breaks into raw text. */
[data-testid="stIconMaterial"] {
    font-family: 'Material Symbols Rounded' !important;
}
/* Long-form READING text gets a cleaner terminal face: multi-paragraph replies in
   VT323 are dense and tiring, so the message bubbles (and the working-notes
   expander body) use Share Tech Mono while VT323/Press Start 2P stay for the
   chrome — headings, marquee, buttons, labels. Placed after the global override;
   the descendant selectors outweigh its bare element selectors. */
.rec-card, .rec-card *, .user-bubble, .user-bubble *,
[data-testid="stExpander"] p, [data-testid="stExpander"] li {
    font-family: 'Share Tech Mono', monospace !important;
}
html, body { font-size: 18px; }
/* Press Start 2P is reserved for short hero text (marquee title, buttons). It is
   too coarse for headings at phone sizes, so headings use VT323 (see below). */
.gg-title, .stButton button {
    font-family: 'Press Start 2P', monospace !important;
}
.stApp {
    background:
        repeating-linear-gradient(
            0deg, rgba(0,0,0,0.24) 0, rgba(0,0,0,0.24) 1px,
            transparent 1px, transparent 3px),
        radial-gradient(circle at 50% 0%, var(--arcade-bg-2) 0%, var(--arcade-bg) 70%);
    color: var(--arcade-neon-cyan);
}
/* CRT-cabinet vignette: the screen edges darken inward like curved glass. A static,
   click-through overlay (pointer-events:none) over the whole viewport. */
.stApp::after {
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 90;
    background: radial-gradient(ellipse 78% 78% at 50% 42%, transparent 58%, rgba(0,0,0,0.5) 100%);
}
/* Marquee "attract mode": the neon glow breathes slowly. Off under reduced-motion. */
@media (prefers-reduced-motion: no-preference) {
    .gg-marquee { animation: gg-attract 3.4s ease-in-out infinite; }
    @keyframes gg-attract {
        0%, 100% { box-shadow: 0 0 14px rgba(45,226,230,0.5),
            inset 0 0 18px rgba(255,46,151,0.25); }
        50% { box-shadow: 0 0 24px rgba(45,226,230,0.85),
            inset 0 0 28px rgba(255,46,151,0.45); }
    }
}
/* The app's own dark is the page-wide background, and the top toolbar is cleared, so
   Streamlit's default near-black (#0e1117) never shows through as a black rectangle —
   under the marquee on desktop, in the gutters, or during a load/reconnect. */
html, body, [data-testid="stAppViewContainer"] {
    background-color: var(--arcade-bg) !important;
}
[data-testid="stHeader"] { background: transparent !important; }
/* Invisible helper components (browser-timezone detection, scroll-to-top) must stay
   in the DOM to run their JS, but must take NO layout space — otherwise their iframe
   shows as a black bar/gap under the marquee. Collapse any element container holding
   an iframe to zero height. */
[data-testid="stElementContainer"]:has(iframe) {
    height: 0 !important; min-height: 0 !important;
    margin: 0 !important; padding: 0 !important; overflow: hidden !important;
}
/* On the empty "attract" screen (only there does .chat-intro exist), centre the
   arcade content vertically so it doesn't sit top-heavy with a void below on desktop. */
[data-testid="stMainBlockContainer"]:has(.chat-intro) {
    display: flex; flex-direction: column; justify-content: center;
    min-height: calc(100vh - 11rem);
}
/* Headings: pink and glowing for the arcade look, but in VT323 with a tight
   glow so they stay crisp and readable — even on a small phone. (Press Start 2P
   here smeared into an unreadable blur at mobile sizes.) */
h1, h2, h3 {
    color: var(--arcade-neon-pink);
    text-shadow: 0 0 4px var(--arcade-neon-pink);
    letter-spacing: 1px; line-height: 1.1;
}
h1 { font-size: 2.6rem; }
h2 { font-size: 2.1rem; }
h3 { font-size: 1.8rem; }
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
/* Agent speech bubble / recommendation "playfield" card (left side). */
.rec-card {
    border: 3px solid var(--arcade-neon-cyan); border-radius: 14px 14px 14px 2px;
    box-shadow: 0 0 12px var(--arcade-neon-cyan), inset 0 0 14px rgba(45,226,230,0.22);
    /* Share Tech Mono runs wider than VT323, so it reads well smaller. */
    padding: 1rem 1.1rem; background: rgba(13,2,33,0.88); font-size: 1.02rem; line-height: 1.6;
}
/* User speech bubble (right side). */
.user-bubble {
    display: inline-block; max-width: 88%; text-align: left;
    background: linear-gradient(180deg, rgba(255,46,151,0.28), rgba(255,46,151,0.12));
    border: 2px solid var(--arcade-neon-pink); border-radius: 14px 14px 2px 14px;
    box-shadow: 0 0 8px rgba(255,46,151,0.45); padding: 0.5rem 0.85rem;
    color: var(--arcade-neon-yellow); font-size: 1.0rem; line-height: 1.5;
}
/* Conversation alignment: a message containing a user bubble flips to the right. */
[data-testid="stChatMessage"]:has(.user-bubble) { flex-direction: row-reverse; }
[data-testid="stChatMessage"]:has(.user-bubble) .stMarkdown { text-align: right; }
/* Bumper buttons — regular buttons AND popover triggers share one look. */
.stButton button, [data-testid="stPopover"] button {
    background: var(--arcade-neon-pink); color: #0d0221; border-radius: 8px;
    border: 2px solid var(--arcade-neon-yellow); box-shadow: 0 4px 0 #b3005f;
    transition: transform 0.05s ease; min-height: 2.6rem;
}
.stButton button:hover, [data-testid="stPopover"] button:hover {
    background: var(--arcade-neon-yellow); }
.stButton button:active, [data-testid="stPopover"] button:active {
    transform: translateY(3px); box-shadow: 0 1px 0 #b3005f; }
/* Visible keyboard focus ring on every interactive control (accessibility). */
.stButton button:focus-visible, [data-testid="stPopover"] button:focus-visible,
input:focus-visible, textarea:focus-visible, select:focus-visible,
[role="radiogroup"] :focus-visible {
    outline: 3px solid var(--arcade-neon-yellow) !important; outline-offset: 2px !important; }
/* The per-message row is transparent (no box/glow) and vertically centers the
   avatar against the bubble; only the inner bubbles (.user-bubble, .rec-card)
   carry borders, so the chat reads as a conversation. */
/* Rows are transparent; the avatar sits at the TOP of the bubble (not floating at the
   centre of a tall reply card) so it reads as the speaker of that message. */
.stChatMessage { border: none !important; background: transparent !important;
    box-shadow: none !important; align-items: flex-start !important;
    margin-bottom: 0.5rem !important; }
[data-testid*="vatar" i] { margin-top: 0.35rem !important; }
/* Bigger, borderless space-invader avatars (no circle/box). Case-insensitive
   match so it works regardless of the exact avatar test id. */
[data-testid*="vatar" i] {
    background: transparent !important; border: none !important; box-shadow: none !important; }
[data-testid*="vatar" i] > * { font-size: 2rem !important; line-height: 1 !important; }
/* Transient "thinking" note shown while the agent works, then cleared. Smaller and
   italic so the model's full working thought reads as an aside; wraps over as many
   lines as it needs (no truncation) within a comfortable measure. */
.gg-thinking { color: var(--arcade-neon-cyan); font-size: 0.95rem; opacity: 0.8;
    font-style: italic; line-height: 1.35; padding: 0.2rem 0; max-width: 60ch; }
/* Big, glowing "added to library" check, centered in the column to line up with
   the full-width ➕ button it replaces. */
.gg-added { color: var(--arcade-neon-green); font-size: 2.4rem; line-height: 2.4rem;
    text-align: center; text-shadow: 0 0 8px var(--arcade-neon-green); }
.stChatInputContainer, [data-testid="stChatInput"] {
    border-top: 2px solid var(--arcade-neon-pink); }
/* Arcade-style chat input: a neon-framed box that glows cyan on focus, matching the
   bumper buttons instead of looking like a default dark field. */
[data-testid="stChatInput"] textarea {
    border: 2px solid rgba(255,46,151,0.6) !important; border-radius: 10px !important;
    box-shadow: 0 0 10px rgba(255,46,151,0.22) !important; }
[data-testid="stChatInput"]:focus-within textarea {
    border-color: var(--arcade-neon-cyan) !important;
    box-shadow: 0 0 14px rgba(45,226,230,0.5) !important; }
/* Opaque purple under the pinned input. It must span the FULL viewport width
   (the outer stBottom), not just the centered block — otherwise the gutters show
   through as black boxes at the bottom corners. */
[data-testid="stBottom"], [data-testid="stBottom"] > div,
[data-testid="stBottomBlockContainer"] {
    background: var(--arcade-bg) !important; }
/* Captions/hints: readable muted cyan instead of the low-contrast default gray. */
[data-testid="stCaptionContainer"], .stCaption { color: rgba(45,226,230,0.8) !important; }
/* In-flow spacer below the conversation so the last reply clears the pinned bar. */
.gg-spacer { height: 6rem; }
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
    .gg-spacer { height: 4.5rem; }
}
/* Small phones (e.g. iPhone SE, ~375px): a notch smaller again. */
@media (max-width: 400px) {
    html, body { font-size: 12px !important; }
    .gg-marquee .gg-title { font-size: 0.8rem; letter-spacing: 0; }
    .gg-marquee .gg-sub { font-size: 0.7rem; letter-spacing: 0; }
    .chat-intro { font-size: 0.95rem; }
    .rec-card { font-size: 1rem; padding: 0.55rem; }
}
/* Respect users who prefer reduced motion: drop the button press animation. */
@media (prefers-reduced-motion: reduce) {
    .stButton button, [data-testid="stPopover"] button { transition: none !important; }
    .stButton button:active, [data-testid="stPopover"] button:active {
        transform: none !important; }
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
