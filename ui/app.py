"""Streamlit application entry point (Req 9, 10, 12).

Sets page config, injects the retro arcade/pinball theme, renders the sidebar
(view switch + optional Gmail import), and routes to the chat or library view.
The layout is "centered" so it reads well on both desktop and phone (Req 9.2).
"""

from __future__ import annotations

import streamlit as st

from ui.bootstrap import detect_and_store_region
from ui.chat_view import render_chat_view
from ui.library_view import render_library_view
from ui.sidebar import render_sidebar
from ui.theme import inject_retro_theme, render_marquee


def main() -> None:
    """Render the GameGusto app for one Streamlit run."""
    st.set_page_config(page_title="GameGusto", page_icon="🕹️", layout="centered")
    inject_retro_theme()
    render_marquee()
    detect_and_store_region()  # once per run, before any get_context() rebuilds
    view = render_sidebar()
    if view == "library":
        render_library_view()
    else:
        render_chat_view()


if __name__ == "__main__":
    main()
