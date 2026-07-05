"""Library / dashboard view (Req 9.4, 9.5).

Manage owned platforms, add/edit games by hand (manual entry + Tavily
autocomplete), browse the library grouped/filterable by platform, and review
recommendation history — all writing to the same store every record source uses.
"""

from __future__ import annotations

import html

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
        cols = st.columns([5, 1])
        edited = cols[0].text_input(
            "name",
            value=platform.name,
            key=f"edit_{platform.platform_id}",
            label_visibility="collapsed",
        )
        if cols[1].button(
            "🗑️",
            key=f"rm_{platform.platform_id}",
            help="Remove this platform",
            use_container_width=True,
        ):
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
            cols[0].markdown(
                f'<div class="lib-line">🎮 {html.escape(suggestion)}</div>',
                unsafe_allow_html=True,
            )
            if suggestion.casefold() in owned:
                cols[1].markdown('<div class="gg-added">✓</div>', unsafe_allow_html=True)
            elif cols[1].button(
                "➕",
                key=f"sugadd_{suggestion}",
                help="Add to your library",
                use_container_width=True,
            ):
                _add_game(memory, user_id, suggestion, platforms)


def _add_game(memory: object, user_id: str, title: str, platforms: list[str]) -> None:
    """Persist a manual game, toast confirmation, and rerun (so it shows as added)."""
    memory.upsert_record(  # type: ignore[attr-defined]
        user_id, GameRecord(title=title, platforms=platforms, source="manual")
    )
    st.toast(f"Added “{title}” to your library")
    st.rerun()


def _render_library(memory: object, user_id: str) -> None:
    """Show owned games (searchable, platform-filterable) with per-row actions."""
    st.subheader("📚 My Library")
    all_records = memory.get_records(user_id)  # type: ignore[attr-defined]
    if not all_records:
        st.caption("No games yet — add one above, or import from Gmail via the CLI.")
        return
    owned = [p.name for p in memory.get_platform_list(user_id)]  # type: ignore[attr-defined]
    filter_col, search_col = st.columns(2)
    choice = filter_col.selectbox("Filter by platform", ["All", *owned])
    query = search_col.text_input("Search", key="lib_search", placeholder="Title or genre…").strip()
    view = all_records
    if choice != "All":
        view = [r for r in view if any(platforms_match(choice, p) for p in r.platforms)]
    if query:
        needle = query.casefold()
        view = [
            r
            for r in view
            if needle in r.title.casefold() or (r.genre and needle in r.genre.casefold())
        ]
    st.caption(f"{len(view)} game(s) · 🕹️ set/change platform · ✨ enrich details")
    suggestions = _platform_suggestions(owned, all_records)

    # Mutating a record then persisting the WHOLE list keeps edits that change the
    # dedup key (e.g. adding a platform) from leaving a stale duplicate behind.
    for index, record in enumerate(sorted(view, key=lambda r: r.title.casefold())):
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
        info, plat_col, enrich_col = st.columns([6, 1, 1])
        info.markdown(
            f'<div class="lib-line">🎮 <b>{html.escape(record.title)}</b> — '
            f"{html.escape(meta) or 'no details yet'}</div>",
            unsafe_allow_html=True,
        )
        _platform_control(plat_col, memory, user_id, all_records, record, index, suggestions)
        if not record.is_enriched() and enrich_col.button(
            "✨",
            key=f"enrich_{index}",
            help="Enrich genre, playtime, platforms & reviews",
            use_container_width=True,
        ):
            with st.spinner(f"Enriching {record.title}…"):
                get_enricher().enrich(record)
            memory.store_records(user_id, all_records)  # type: ignore[attr-defined]
            st.rerun()


def _platform_suggestions(owned: list[str], records: list) -> list[str]:
    """Platforms the user has actually used: the owned list + values on any record."""
    names = set(owned)
    for record in records:
        names.update(p for p in record.platforms if p)
    return sorted(names, key=str.casefold)


def _platform_control(
    column: object,
    memory: object,
    user_id: str,
    all_records: list,
    record: object,
    index: int,
    suggestions: list[str],
) -> None:
    """A 🕹️ popover to set or change a record's platform from the existing list.

    Picking from platforms you already use avoids typos; "Other…" allows a new one.
    """
    current = record.platforms[0] if record.platforms else ""  # type: ignore[attr-defined]
    with column.popover(  # type: ignore[attr-defined]
        "🕹️", help="Set or change the platform you own this on", use_container_width=True
    ):
        st.caption(f"Current: {current}" if current else "No platform set yet")
        options = [*suggestions, "Other…"]
        default = options.index(current) if current in suggestions else len(options) - 1
        pick = st.selectbox(
            "Platform", options, index=default, key=f"setplat_{index}", label_visibility="collapsed"
        )
        value = pick
        if pick == "Other…":
            value = st.text_input(
                "New platform", key=f"setplatother_{index}", placeholder="e.g. Steam Deck"
            ).strip()
        if st.button("Save", key=f"setplatsave_{index}", use_container_width=True) and value:
            record.platforms = [value]  # type: ignore[attr-defined]
            memory.store_records(user_id, all_records)  # type: ignore[attr-defined]
            st.toast(f"Platform for “{record.title}”: {value}")  # type: ignore[attr-defined]
            st.rerun()


#: Feedback verdict -> the badge shown next to a pick that carries it.
_VERDICT_BADGES = {"loved": "💚 loved", "not_for_me": "🚫 not for me"}


def _render_history(memory: object, user_id: str) -> None:
    """Show recent recommendations with one-tap feedback and add-to-library (Req 9.4).

    👍/👎 record whether a pick landed ("loved" / "not_for_me") — tapping the same
    verdict again clears it. The agent reads this feedback via
    ``get_recent_recommendations`` and weighs it as a taste signal on future picks.
    """
    title_col, clear_col = st.columns([4, 1])
    title_col.subheader("🏆 Recent Picks")
    recs = memory.get_recent_recommendations(user_id, 10)  # type: ignore[attr-defined]
    if not recs:
        st.caption("No recommendations yet — head to the chat and ask for one.")
        return
    # Clearing the history frees the agent to suggest these titles again (e.g. a
    # pick skipped today may be exactly right in a few months). Feedback verdicts
    # are kept — they are taste, not recency. Behind a confirm to avoid stray taps.
    with clear_col.popover("🧹", help="Clear the picks history", use_container_width=True):
        st.caption("Forget these picks so they can be recommended again? (👍/👎 stay)")
        if st.button("Yes, clear the history", key="clear_picks", use_container_width=True):
            memory.clear_recent_recommendations(user_id)  # type: ignore[attr-defined]
            st.rerun()
    st.caption("👍 loved it · 👎 not for me (the agent learns from this) · ➕ add to library")
    owned = {r.title.casefold() for r in memory.get_records(user_id)}  # type: ignore[attr-defined]
    feedback = memory.get_feedback(user_id)  # type: ignore[attr-defined]
    seen: set[str] = set()
    for index, rec in enumerate(recs):
        key = rec.game_title.strip().casefold()
        if key in seen:  # the same game may be recommended across sessions; show it once
            continue
        seen.add(key)
        verdict = (feedback.get(key) or {}).get("verdict")
        badge = f" <small>{_VERDICT_BADGES[verdict]}</small>" if verdict else ""
        info, up_col, down_col, add_col = st.columns([4, 1, 1, 1])
        info.markdown(
            f'<div class="hist-line">🎯 <b>{html.escape(rec.game_title)}</b>{badge}</div>',
            unsafe_allow_html=True,
        )
        if up_col.button(
            "👍",
            key=f"fb_up_{index}",
            help="Loved it — recommend more like this",
            use_container_width=True,
        ):
            _set_feedback(memory, user_id, rec.game_title, "loved", verdict)
        if down_col.button(
            "👎",
            key=f"fb_down_{index}",
            help="Not for me — steer away from picks like this",
            use_container_width=True,
        ):
            _set_feedback(memory, user_id, rec.game_title, "not_for_me", verdict)
        if key in owned:
            add_col.markdown('<div class="gg-added">✓</div>', unsafe_allow_html=True)
        elif add_col.button(
            "➕",
            key=f"pickadd_{index}_{rec.game_title}",
            help="Add to your library",
            use_container_width=True,
        ):
            _add_game(memory, user_id, rec.game_title, [])


def _set_feedback(
    memory: object, user_id: str, title: str, verdict: str, current: str | None
) -> None:
    """Set or (when tapped again) clear a pick's feedback verdict, then rerun."""
    new_verdict = None if current == verdict else verdict
    memory.set_feedback(user_id, title, new_verdict)  # type: ignore[attr-defined]
    st.rerun()
