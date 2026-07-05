"""Smoke tests for the pure UI helpers (Req 9.1, 9.2, 9.3).

Streamlit render functions need a running app context and are covered by manual /
deploy verification; here we test only the import-safe pure helpers: the theme
CSS, the chat card/label helpers, and the secrets→env mapping.
"""

from __future__ import annotations

from ui.bootstrap import _region_from_timezone, _secrets_to_env
from ui.chat_view import _card_html, _strip_leading_rule, _tool_label
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
