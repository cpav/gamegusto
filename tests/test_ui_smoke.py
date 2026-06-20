"""Smoke tests for the pure UI helpers (Req 9.1, 9.2, 9.3).

Streamlit render functions need a running app context and are covered by manual /
deploy verification; here we test only the import-safe pure helpers: the theme
CSS, the chat card/label helpers, and the secrets→env mapping.
"""

from __future__ import annotations

from ui.bootstrap import _secrets_to_env
from ui.chat_view import _card_html, _tool_label
from ui.theme import MARQUEE_HTML, RETRO_ARCADE_CSS


def test_theme_has_pixel_font_and_responsive_rule() -> None:
    assert "Press Start 2P" in RETRO_ARCADE_CSS
    assert "@media" in RETRO_ARCADE_CSS  # responsive for phone (Req 9.2)
    assert ".rec-card" in RETRO_ARCADE_CSS
    assert "GAMEGUSTO" in MARQUEE_HTML


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
