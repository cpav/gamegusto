"""Headless conversation CLI for GameGusto (no UI).

Runs a real conversation with the Bedrock-backed agent against the live
DynamoDB-backed library. The agent decides the flow: just talk to it — describe
what you feel like playing, how much time you have, the genre or vibe you want —
and it will check your platforms and library, look things up as needed, and
recommend a game with reasoning and alternatives. Follow-ups like "I already
played it" or "something shorter" continue the same conversation.

The slash commands below are convenience shortcuts for managing data directly;
everything they do is also available to the agent through its tools.

Run inside the venv with AWS credentials, ``BEDROCK_MODEL_ID``, and
``DYNAMODB_TABLE_NAME`` configured (see .env.example):
    python cli.py

Commands:
    /help                       show this help
    /platform add <name>        add an owned platform
    /platform list              list owned platforms
    /platform rm <id>           remove an owned platform by id
    /add <title> :: <platform>  add a game by hand (manual source)
    /refresh                    import from sources (Gmail + manual), enrich, persist
    /library                    list stored games
    /reset                      start a new conversation
    /quit                       exit
Anything else is sent to the agent as conversation.
"""

from __future__ import annotations

import sys

from bootstrap import AppContext, build_app
from config import Config, ConfigError, load_env_file
from models.game_record import GameRecord
from models.platform import OwnedPlatform
from services.bedrock_service import BedrockServiceError


def _handle_conversation(ctx: AppContext, text: str) -> None:
    """Send free text to the agent and print its response."""
    try:
        reply = ctx.runtime.send(text)
    except BedrockServiceError as exc:
        print(f"\nagent: {exc}")
        return
    print(f"\nagent: {reply.message}")
    if reply.is_stateless_mode:
        print("  (memory unavailable — personalization is limited this session)")
    if reply.tool_calls:
        print(f"  [used: {', '.join(reply.tool_calls)}]")


def _handle_platform(ctx: AppContext, args: str) -> None:
    """Handle '/platform add|list|rm' subcommands."""
    parts = args.split(maxsplit=1)
    action = parts[0] if parts else ""
    rest = parts[1] if len(parts) > 1 else ""
    if action == "add" and rest:
        ctx.memory.add_platform(ctx.user_id, OwnedPlatform(name=rest.strip()))
        print(f"Added platform: {rest.strip()}")
    elif action == "list":
        platforms = ctx.memory.get_platform_list(ctx.user_id)
        if not platforms:
            print("No platforms yet. Add one with '/platform add <name>'.")
        for platform in platforms:
            print(f"  {platform.platform_id}  {platform.name}")
    elif action == "rm" and rest:
        ok = ctx.memory.remove_platform(ctx.user_id, rest.strip())
        print("Removed." if ok else "No platform with that id.")
    else:
        print("Usage: /platform add <name> | /platform list | /platform rm <id>")


def _handle_add_game(ctx: AppContext, args: str) -> None:
    """Handle '/add <title> :: <platform>' manual entry."""
    if "::" not in args:
        print("Usage: /add <title> :: <platform>")
        return
    title, _, platform = args.partition("::")
    title, platform = title.strip(), platform.strip()
    if not title or not platform:
        print("Usage: /add <title> :: <platform>")
        return
    record = GameRecord(title=title, platforms=[platform], source="manual")
    ctx.memory.upsert_record(ctx.user_id, record)
    print(f"Added '{title}' on {platform}. Run /refresh to enrich it.")


def _handle_refresh(ctx: AppContext) -> None:
    """Run library assembly (sources -> dedup -> enrich -> persist)."""
    print("Refreshing library (importing, enriching)...")
    records = ctx.library.refresh(ctx.user_id)
    print(f"Library now has {len(records)} game(s).")
    if ctx.gmail is not None and not ctx.gmail.is_available():
        print(f"  Gmail import skipped: {ctx.gmail.last_error or 'not connected'}")


def _handle_library(ctx: AppContext) -> None:
    """List the stored library."""
    records = ctx.memory.get_records(ctx.user_id)
    if not records:
        print("Library is empty. Add games with '/add' or '/refresh'.")
        return
    for record in records:
        enriched = "enriched" if record.is_enriched() else "needs enrichment"
        print(f"  {record.title} [{', '.join(record.platforms)}] ({record.source}, {enriched})")


def _dispatch(ctx: AppContext, line: str) -> bool:
    """Route one input line. Returns False when the user asks to quit."""
    if line in ("/quit", "/exit"):
        return False
    if line == "/help":
        print(__doc__)
    elif line == "/reset":
        ctx.runtime.reset()
        print("New conversation. What are you in the mood to play?")
    elif line.startswith("/platform"):
        _handle_platform(ctx, line[len("/platform") :].strip())
    elif line.startswith("/add"):
        _handle_add_game(ctx, line[len("/add") :].strip())
    elif line == "/refresh":
        _handle_refresh(ctx)
    elif line == "/library":
        _handle_library(ctx)
    elif line.startswith("/"):
        print("Unknown command. Type /help.")
    else:
        _handle_conversation(ctx, line)
    return True


def main() -> None:
    """Build the app from the environment and run the conversation loop."""
    load_env_file()
    try:
        config = Config.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        sys.exit(1)

    ctx = build_app(config)
    print("GameGusto (headless). Type /help for commands, /quit to exit.")
    print("Tell me what you're in the mood to play.\n")

    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if not _dispatch(ctx, line):
            break


if __name__ == "__main__":
    main()
