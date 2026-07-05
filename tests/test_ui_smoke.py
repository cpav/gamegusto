"""Smoke tests for the pure UI helpers (Req 9.1, 9.2, 9.3).

Streamlit render functions need a running app context and are covered by manual /
deploy verification; here we test only the import-safe pure helpers: the theme
CSS, the chat card/label helpers, and the secrets→env mapping.
"""

from __future__ import annotations

from ui.bootstrap import _region_from_timezone, _secrets_to_env
from ui.chat_view import (
    _card_html,
    _clean_reply,
    _strip_leading_narration,
    _strip_leading_rule,
    _tool_label,
)
from ui.theme import MARQUEE_HTML, RETRO_ARCADE_CSS


def test_theme_has_pixel_font_and_responsive_rule() -> None:
    assert "Press Start 2P" in RETRO_ARCADE_CSS
    assert "@media" in RETRO_ARCADE_CSS  # responsive for phone (Req 9.2)
    assert ".rec-card" in RETRO_ARCADE_CSS
    assert "GAMEGUSTO" in MARQUEE_HTML


def test_strip_leading_rule() -> None:
    assert _strip_leading_rule("---\n🎮 Tonight's Pick") == "🎮 Tonight's Pick"
    assert _strip_leading_rule("\n\n***\n\nHi") == "Hi"
    assert _strip_leading_rule("- - -\nBody") == "Body"
    # a rule in the middle (a real divider) is left alone
    assert _strip_leading_rule("Intro\n---\nMore") == "Intro\n---\nMore"
    assert _strip_leading_rule("No rule here") == "No rule here"


def test_strip_leading_narration() -> None:
    # The real case: a process-narration preamble is dropped, the answer kept.
    reply = (
        "I now have a solid picture of both sales. Let me now cross-reference the "
        "on-sale titles against your library.\n\nHere's what I found:\n\n🛒 Deals"
    )
    assert _strip_leading_narration(reply) == "Here's what I found:\n\n🛒 Deals"
    # A conversational lead-in before the opener is peeled too.
    assert _strip_leading_narration("Good — let me pull up the list.\n\n🎯 Hades") == "🎯 Hades"
    # A genuine intro is NOT narration and stays.
    intro = "Based on your library, your taste is clear.\n\n🎯 Pick: Hades"
    assert _strip_leading_narration(intro) == intro
    # "Let me recommend…" is the answer, not gathering — never stripped.
    rec = "Let me recommend Hades — a fast roguelike.\n\nAlternatives: Dead Cells"
    assert _strip_leading_narration(rec) == rec
    # An all-narration reply is left intact (never stripped to empty).
    solo = "Let me check your platforms."
    assert _strip_leading_narration(solo) == solo


def test_clean_reply_strips_rule_then_narration() -> None:
    raw = "---\nI now have enough. Let me compile the picks.\n\n🎮 Tonight's Pick"
    assert _clean_reply(raw) == "🎮 Tonight's Pick"


def test_card_html_wraps_message() -> None:
    html = _card_html("Play **Hades**")
    assert html.startswith('<div class="rec-card">')
    assert "Play **Hades**" in html
    assert html.endswith("</div>")


def test_tool_label_known_and_fallback() -> None:
    assert "web" in _tool_label("web_search").lower()
    # Unknown tool falls back to a readable label rather than raising.
    assert _tool_label("teleport_player") == "🔧 teleport player"


def test_secrets_to_env_keeps_only_strings() -> None:
    secrets = {"AWS_REGION": "eu-north-1", "PORT": 8501, "NESTED": {"k": "v"}}
    assert _secrets_to_env(secrets) == {"AWS_REGION": "eu-north-1"}


def test_region_from_timezone() -> None:
    # Physical location from the browser timezone, regardless of UI language.
    assert _region_from_timezone("Europe/Copenhagen") == "Denmark"
    assert _region_from_timezone("Europe/Berlin") == "Germany"
    assert _region_from_timezone("America/Los_Angeles") == "United States"
    # Unknown/empty zones don't pin a country (the configured default applies).
    assert _region_from_timezone("Mars/Olympus_Mons") is None
    assert _region_from_timezone("") is None
    assert _region_from_timezone(None) is None


def test_daily_starters_are_stable_and_rotate() -> None:
    """Today's starter window is deterministic (no reshuffling on rerun) and sized
    to the 2x2 grid; every configured starter appears across consecutive days."""
    from ui.chat_view import _STARTER_COUNT, _STARTER_PROMPTS, _daily_starters

    today = _daily_starters()
    assert today == _daily_starters()  # stable within the day
    assert len(today) == _STARTER_COUNT
    assert all(label in _STARTER_PROMPTS for label in today)


def test_user_html_escapes_markup() -> None:
    from ui.chat_view import _user_html

    rendered = _user_html("<b>hi</b> & <script>alert(1)</script>")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert rendered.startswith('<div class="user-bubble">')
