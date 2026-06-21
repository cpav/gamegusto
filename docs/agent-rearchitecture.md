# GameGusto → Re-architecture Brief: Tool-Using Bedrock Agent

> Hand-off prompt for the next working session. Goal: replace the current fixed
> mood→time→recommend pipeline with a proper **agent** — Claude Sonnet 4.6 on
> Bedrock as the reasoning core that calls **tools** and decides the flow itself.
> Clean this up as needed, then drive implementation from it.

## 1. Why we're changing direction

Today the conversation is a hardcoded state machine (`AgentOrchestrator`:
mood_gathering → time_gathering → platform gate → recommend). The LLM only
(a) maps text to four mood numbers and (b) writes a blurb about a game that
**deterministic rules already picked** (filter by owned platform + time budget +
not-in-last-5, rank by review). Consequences seen in real testing:

- A request "RPG with a cool job system, 2D HD graphics, solo, challenging,
  story not too complex" was ignored; it recommended a **Shooter** (Far Cry 5).
- "I already played it" dead-ended ("no match") instead of offering another.
- Free-text taste/genre is never used; the LLM doesn't actually choose the game.

**Target:** the LLM is the agent. It interprets the request, decides which tools
to call (and when), asks for missing info only if needed, and produces a
recommendation that honors what the user actually asked for.

## 2. Target architecture

```
User <→ Agent runtime (tool-use loop)
              │  Claude Sonnet 4.6 (Bedrock Converse, extended thinking)
              │  system prompt defines role + how to use tools + when to ask
              ▼
        Tools (typed functions the model may call):
          mood/time helpers · platform CRUD · library read/search ·
          manual add · gmail import · tavily enrich/web search ·
          recent-recommendations · save session
              ▼
        Services (UNCHANGED): MemoryService(DynamoDB) · TavilyService ·
          GmailSource · BedrockService
```

The fixed phases disappear. The agent loop runs until the model emits a final
answer; clarifying questions, recommendations, and follow-ups ("something else",
"shorter", "I played it") are all just turns in one conversation.

## 3. AWS implementation options (pick one)

**Option A — Bedrock Converse API "tool use" (RECOMMENDED, incremental).**
We already call `bedrock-runtime` `converse`. Add a `toolConfig` with tool specs
and run a tool-use loop in our code:

- Call `converse(modelId, messages, toolConfig={"tools": [...]}, system=[...],
  additionalModelRequestFields={"thinking": {...}})`.
- Each `toolSpec` = `{name, description, inputSchema: {json: <JSON schema>}}`.
- If `response["stopReason"] == "tool_use"`: read `toolUse` blocks
  (`{toolUseId, name, input}`) from `output.message.content`, append that
  assistant message, execute each tool, then append a `user` message containing
  `toolResult` blocks (`{toolUseId, content:[{json|text}], status}`). Loop.
- Stop when `stopReason == "end_turn"`; the text blocks are the reply.
- Note: verify **extended thinking + tool use** compatibility for Sonnet 4.6 on
  Bedrock (interleaved thinking). If it complicates the loop, either use
  interleaved-thinking or run tool loops without thinking and keep thinking for
  the final reasoning. Search: "Amazon Bedrock Converse API tool use",
  "Anthropic Claude tools Bedrock", "interleaved thinking tool use".

**Option B — Bedrock Agents / AgentCore with action groups (managed).**
Define an agent with action groups backed by Lambda/OpenAPI; AWS runs the
orchestration loop. More infra (agent, alias, Lambda, IAM), less code. Consider
later if we want managed memory/observability. Search: "Amazon Bedrock Agents
action groups", "Bedrock AgentCore", "return of control".

Recommendation: **Option A** now (keeps everything local, no new infra, reuses
`BedrockService`), revisit B if we outgrow it.

## 4. Tools to expose (wrap EXISTING code; keep services as-is)

Each tool = thin function + JSON schema. Implementations already exist:

| Tool | Input | Returns | Backed by |
|---|---|---|---|
| `get_owned_platforms` | – | list of {id,name} | `MemoryService.get_platform_list` |
| `add_platform` / `remove_platform` | name / id | ok | `MemoryService` |
| `get_library` | optional filters (platform, genre, has_playtime) | list of GameRecord dicts | `MemoryService.get_records` (+ filter) |
| `add_manual_game` | title, platform, optional playtime/genre | ok | `MemoryService.upsert_record` |
| `set_game_fields` | title, {estimated_playtime, genre, ...} | ok | `MemoryService.upsert_record` (manual playtime fill) |
| `import_gmail` | – | imported count | `LibraryService.refresh` / `GmailSource` |
| `enrich_game` | title | enriched fields | `TavilyService.enrich` |
| `web_search` | query | snippets (genre/playtime/availability/review) | `TavilyService` (internet access) |
| `find_deals` | title, optional platforms | per-store deal snippets (price/discount) | `agent/deals.py` → `TavilyService.web_search` (official stores, region-scoped) |
| `get_recent_recommendations` | n | titles | `MemoryService.get_recent_recommendations` |
| `save_recommendation` | game, reasoning, mood, time | ok | `MemoryService.store_session` |
| `parse_time` (optional) | text | minutes / needs_clarification | `TimeParser` |
| `interpret_mood` (optional) | text | dimensions / needs_clarification | `MoodInterpreter` |

Notes:
- `interpret_mood`/`parse_time` can stay as tools, OR the model can reason about
  mood/time itself and only use tools for **data/actions** (library, platforms,
  enrichment, persistence). Prefer the latter: fewer tools, the model already
  understands "relaxed" / "about 2 hours". Keep `parse_time` only if we want
  strict numeric budgets.
- Selection (the recommendation) is the **model's job**, not a tool: it reads the
  library via `get_library`, applies the user's taste + mood + time + owned
  platforms, can `web_search`/`enrich_game` to fill gaps, and returns the pick
  with reasoning. It may use its own knowledge of titles (it knows which library
  games are job-system RPGs even if enrichment mislabeled the genre).

## 5. Keep / reuse (do NOT rebuild)

- `models/game_record.py` (contract v2.0.0), `models/*`.
- `services/`: `MemoryService` + `DynamoDBMemoryClient` (DynamoDB table
  `gamegusto`, region from `.env`), `TavilyService`, `GmailSource`
  (Nintendo + Microsoft parsers, read-only), `BedrockService`, `ErrorHandler`.
- `config.py` (`load_env_file`, `BEDROCK_MODEL_ID=eu.anthropic.claude-sonnet-4-6`,
  `DYNAMODB_TABLE_NAME`, etc.), `bootstrap.build_app`, `cli.py` shell.
- Hard-LLM-dependency policy (errors, no mock fallback); graceful degradation for
  memory/Tavily/Gmail.

## 6. Replace / change

- **`agent/orchestrator.py`** → an `AgentRuntime` that owns the Converse tool-use
  loop, a system prompt, the tool registry, and conversation history. Remove the
  phase state machine.
- **`agent/recommender.py`** → demote to helper data (eligibility filtering can
  become the `get_library` filter args); the *choice* moves into the agent. Keep
  pure helpers (`_is_playable`, review parsing) if useful as a `rank_candidates`
  tool, but don't force review-only ranking.
- **`BedrockService`** → add `converse_with_tools(messages, tools, system)` (or a
  `ToolLoop` helper) alongside the existing methods.
- `MoodInterpreter`/`TimeParser` → keep as optional tools or delete if the model
  handles intake natively.

## 7. Concrete issues from testing to fix in the new design

1. **Honor stated taste/genre** (the core failure) — handled naturally once the
   model selects using the full request.
2. **Feedback loop**: "I already played it" / "something else" / "shorter" must
   exclude the current pick and re-recommend within the same conversation.
3. **Playtime semantics**: enrichment returns *completion* time (e.g. 600 min =
   10h), which reads wrong against a *session* budget. Decide: store
   completion-time separately and let the agent reason ("you have 2h tonight, this
   is a 40h game but plays well in short sessions"), or treat budget as session
   length. The user will fill `estimated_playtime` manually for owned games —
   expose `set_game_fields`.
4. **Platform-name granularity**: owned "Xbox" won't match availability "Xbox
   One"/"Xbox Series X" (exact casefold compare). Normalize to platform families
   (Xbox, PlayStation, Nintendo, PC) in matching, or let the agent reason about it.
5. **Enrichment genre quality** is rough; rely on the model's title knowledge +
   `web_search` rather than trusting `genre` blindly.

## 8. Spec updates

- **requirements.md**: revise Req 1 (intake is conversational/agent-driven, not a
  fixed prompt sequence) and Req 7 (recommendation must reflect the user's stated
  taste/genre, not just mood/time/review). Add a requirement: "The agent selects
  and acts via tools (platforms, library, enrichment, web, persistence) and asks
  for missing info only when needed."
- **design.md**: replace the phase-flow + deterministic Recommender sections with
  the agent + tool-use architecture (Section 2–4 above). Keep services/data
  contract sections.
- **tasks.md**: new tasks — (a) `converse_with_tools` in BedrockService + tool
  loop; (b) tool registry wrapping services; (c) AgentRuntime + system prompt;
  (d) feedback handling; (e) `set_game_fields`/manual playtime; (f) platform-family
  matching; (g) tests (tool dispatch, a scripted multi-turn e2e with the network
  edge faked); then the Streamlit UI on top.

## 9. Definition of done (acceptance scenario)

Free-form conversation, no fixed prompts:

> "I want an RPG with a cool job system, 2D HD graphics, solo, challenging, story
> not too complex, ~30h."

The agent: checks owned platforms, queries the library, uses its knowledge +
`web_search`/`enrich_game` to identify matching owned titles (e.g. Octopath
Traveler-style), returns one strong pick with reasoning + alternatives, and on
"I already played it" offers the next best — all without re-asking everything.

## 10. Guardrails (unchanged)

- Keep ruff/mypy clean, fast tests green (Hypothesis "fast" profile), pre-commit
  hooks; feature branch → PR → squash-merge (gh CLI at `~/.local/bin/gh`); never
  commit `.env`/secrets; DynamoDB table already provisioned; LLM verified live via
  `scripts/check_llm.py`.
