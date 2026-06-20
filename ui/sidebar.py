"""Sidebar: view switch + optional Gmail import (Req 9.6).

The chat/library switch always shows. Gmail import controls appear only when a
Gmail source is configured — on the hosted deployment Gmail is unset, so they are
omitted and the app relies on the existing library + manual entry (Req 9.6, 12.4).
"""

from __future__ import annotations

import streamlit as st

from ui.bootstrap import get_context, get_user_id


def render_sidebar() -> str:
    """Render sidebar controls; return the selected view ("chat" | "library")."""
    with st.sidebar:
        st.markdown("### 🎯 GameGusto")
        view = st.radio("View", ["chat", "library"], horizontal=True, format_func=str.capitalize)
        if st.button("🔄 New conversation"):
            get_context().runtime.reset()
            st.session_state["messages"] = []
            st.rerun()
        _render_gmail_import()
    return view


def _render_gmail_import() -> None:
    """Show Gmail import only when configured; report imported count (Req 9.6, 10.5)."""
    ctx = get_context()
    if ctx.gmail is None:
        return
    st.markdown("---")
    st.markdown("#### 📧 Gmail Purchases")
    if st.button("Import purchases"):
        with st.spinner("Reading purchase confirmations…"):
            before = len(ctx.memory.get_records(get_user_id()))
            records = ctx.library.refresh(get_user_id())
        if not ctx.gmail.is_available():
            st.warning(ctx.gmail.last_error or "Couldn't read Gmail right now.")
        else:
            st.success(f"Imported {max(0, len(records) - before)} new game(s).")
