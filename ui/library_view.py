"""Library / dashboard view (Req 9.4, 9.5).

Manage owned platforms, add/edit games by hand (manual entry + Tavily
autocomplete), browse the library grouped/filterable by platform, and review
recommendation history — all writing to the same store every record source uses.
"""

from __future__ import annotations

import streamlit as st

from agent.platform_match import platforms_match
from models.game_record import GameRecord
from models.platform import OwnedPlatform
from ui.bootstrap import get_autocomplete, get_enricher, get_memory_service, get_user_id


def render_library_view() -> None:
    """Render platform manager, add-game, the library, and history."""
    memory = get_memory_service()
    user_id = get_user_id()
    _render_platform_manager(memory, user_id)
    _render_add_game(memory, user_id)
    _render_library(memory, user_id)
    _render_history(memory, user_id)


def _render_platform_manager(memory: object, user_id: str) -> None:
    """Add / rename / remove owned platforms (Req 6.1, 6.4)."""
    st.subheader("🕹️ My Platforms")
    new = st.text_input("Add a platform", key="add_platform", placeholder="e.g. Nintendo Switch 2")
    if st.button("Add Platform") and new.strip():
        memory.add_platform(user_id, OwnedPlatform(name=new.strip()))  # type: ignore[attr-defined]
        st.rerun()

    for platform in memory.get_platform_list(user_id):  # type: ignore[attr-defined]
        cols = st.columns([4, 1])
        edited = cols[0].text_input(
            "name",
            value=platform.name,
            key=f"edit_{platform.platform_id}",
            label_visibility="collapsed",
        )
        if cols[1].button("Remove", key=f"rm_{platform.platform_id}"):
            memory.remove_platform(user_id, platform.platform_id)  # type: ignore[attr-defined]
            st.rerun()
        elif edited.strip() and edited != platform.name:
            memory.update_platform(user_id, platform.platform_id, edited.strip())  # type: ignore[attr-defined]
            st.rerun()


def _render_add_game(memory: object, user_id: str) -> None:
    """Add a game by hand or one-click from autocomplete suggestions (Req 3.4, 9.5)."""
    st.subheader("➕ Add a Game (you own)")
    query = st.text_input("Game title", key="add_game", placeholder="Type 3+ letters…")
    platform = st.text_input("Platform (optional)", key="add_game_platform", placeholder="e.g. PC")
    title = query.strip()
    platforms = [platform.strip()] if platform.strip() else []

    if st.button("Add Game") and title:
        _add_game(memory, user_id, title, platforms)

    if len(title) >= 3:
        owned = {r.title.casefold() for r in memory.get_records(user_id)}  # type: ignore[attr-defined]
        suggestions = get_autocomplete(title)
        if suggestions:
            st.caption("Suggestions — tap ➕ to add to your library")
        for suggestion in suggestions:
            cols = st.columns([5, 1])
            cols[0].markdown(f'<div class="lib-line">🎮 {suggestion}</div>', unsafe_allow_html=True)
            if suggestion.casefold() in owned:
                cols[1].markdown("✓")
            elif cols[1].button("➕", key=f"sugadd_{suggestion}", help="Add to your library"):
                _add_game(memory, user_id, suggestion, platforms)


def _add_game(memory: object, user_id: str, title: str, platforms: list[str]) -> None:
    """Persist a manual game, toast confirmation, and rerun (so it shows as added)."""
    memory.upsert_record(  # type: ignore[attr-defined]
        user_id, GameRecord(title=title, platforms=platforms, source="manual")
    )
    st.toast(f"Added “{title}” to your library")
    st.rerun()


def _render_library(memory: object, user_id: str) -> None:
    """Show owned games grouped/filterable by platform (Req 9.4)."""
    st.subheader("📚 My Library")
    records = memory.get_records(user_id)  # type: ignore[attr-defined]
    if not records:
        st.caption("No games yet — add one above, or import from Gmail via the CLI.")
        return
    owned = [p.name for p in memory.get_platform_list(user_id)]  # type: ignore[attr-defined]
    choice = st.selectbox("Filter by platform", ["All", *owned])
    if choice != "All":
        records = [r for r in records if any(platforms_match(choice, p) for p in r.platforms)]
    st.caption(
        f"{len(records)} game(s) · ✨ enriches genre, playtime, platforms & an averaged review"
    )
    for record in sorted(records, key=lambda r: r.title.casefold()):
        review = record.community_review
        meta = " · ".join(
            part
            for part in (
                record.genre,
                f"~{record.estimated_playtime} min" if record.estimated_playtime else "",
                f"⭐{review.score:.1f}/10 ({review.source_count} sources)" if review else "",
                ", ".join(record.platforms),
            )
            if part
        )
        cols = st.columns([6, 1])
        cols[0].markdown(
            f'<div class="lib-line">🎮 <b>{record.title}</b> — {meta or "no details yet"}</div>',
            unsafe_allow_html=True,
        )
        if not record.is_enriched() and cols[1].button(
            "✨", key=f"enrich_{record.dedup_key}", help="Enrich this game's details"
        ):
            with st.spinner(f"Enriching {record.title}…"):
                get_enricher().enrich(record)
                memory.upsert_record(user_id, record)  # type: ignore[attr-defined]
            st.rerun()


def _render_history(memory: object, user_id: str) -> None:
    """Show recent recommendations, each addable to the library with one click (Req 9.4)."""
    st.subheader("🏆 Recent Picks")
    recs = memory.get_recent_recommendations(user_id, 10)  # type: ignore[attr-defined]
    if not recs:
        st.caption("No recommendations yet — head to the chat and ask for one.")
        return
    st.caption("Tap ➕ to add a pick to your library")
    owned = {r.title.casefold() for r in memory.get_records(user_id)}  # type: ignore[attr-defined]
    seen: set[str] = set()
    for index, rec in enumerate(recs):
        key = rec.game_title.casefold()
        if key in seen:  # the same game may be recommended across sessions; show it once
            continue
        seen.add(key)
        cols = st.columns([5, 1])
        cols[0].markdown(
            f'<div class="hist-line">🎯 <b>{rec.game_title}</b></div>', unsafe_allow_html=True
        )
        if key in owned:
            cols[1].markdown("✓")
        elif cols[1].button(
            "➕", key=f"pickadd_{index}_{rec.game_title}", help="Add to your library"
        ):
            _add_game(memory, user_id, rec.game_title, [])
