# Requirements Document

## Introduction

A Python application that recommends the next video game to **buy and play** based on the user's current mood, available time, gaming taste, and the platforms the user owns. It is a **discovery tool**: it recommends games the user does **not** already own, using their existing library to learn their taste and to exclude titles they already have. A **tool-using conversational agent** — a Claude Sonnet base model on Amazon Bedrock, driven through the Bedrock Runtime Converse API "tool use" loop — is the reasoning core: it interprets the user's request in natural language, decides which tools to call and when (manage platforms, read the owned library for taste/exclusion, import from sources, enrich records, search the web, recall recent recommendations, persist sessions), asks for missing information only when needed, and selects the recommendation itself so the result honors the user's stated taste and genre rather than a fixed deterministic ranking. The user's library is assembled from exactly two record sources — read-only Gmail purchase-confirmation emails (Nintendo eShop, Microsoft Store) and manual entry through the UI/CLI — all normalized into a single canonical Game_Record. Records are enriched via Tavily/the model (genre, playtime, platform availability, community review) and persisted in a DynamoDB-backed memory store so the agent understands the user's taste, avoids recommending owned or recently-suggested games, and favors well-regarded titles playable on hardware the user owns. The model is a required dependency: if the model call fails, the system surfaces a clear error rather than substituting mock or fabricated output, while the memory store and Tavily degrade gracefully. The UI is built with Streamlit, styled as a retro arcade machine, and provides both a conversational chat and a library/dashboard view on desktop and mobile.

## Glossary

- **Recommendation_Agent**: The tool-using conversational AI agent that interprets user input, decides which Tools to call, manages context, and generates game recommendations. It is a Claude Sonnet base model on Amazon Bedrock driven through the Bedrock Runtime Converse API tool-use loop (see Agent_Runtime and Tool).
- **Agent_Runtime**: The component that owns the Converse tool-use loop, the system prompt, the Tool registry, and the conversation history. It runs the model, executes the Tools the model requests, and continues until the model emits a final answer. It replaces the former fixed-phase orchestrator.
- **Tool**: A typed function the model may call during a turn (e.g. get/add/remove platforms, get/update library, import Gmail, enrich game, web search, find store deals, recent recommendations, save recommendation). Each Tool wraps an existing service and is declared to the model with a JSON-schema input. The Tool set is extensible without changing the loop.
- **Streamlit_UI**: The web interface built with Streamlit, providing the chat and library/dashboard views on desktop and mobile.
- **Game_Record**: The single canonical record for one owned game, populated by every record source and used everywhere in the system. Fields include at minimum: title, platform(s), source (one of `gmail`, `manual`, `enrichment`), optional purchase_date, genre, estimated_playtime, community_review, and source/external identifiers. The exact field set is fixed by the Data_Contract.
- **Data_Contract**: The documented, versioned definition of the Game_Record schema (field names, types, required vs. optional, provenance values) that every record source and consumer conforms to.
- **Record_Source**: Any interchangeable origin of Game_Records — Gmail purchase emails or manual entry — each of which produces records conforming to the Data_Contract.
- **Tavily_Enrichment**: The Tavily API integration used to populate Game_Record metadata (genre, estimated_playtime, platform availability, community_review) and to power autocomplete for manual entry.
- **Memory_Store**: The DynamoDB-backed persistent store and system of record that holds Game_Records, past recommendations, mood patterns, owned platforms, and preferences across sessions. The Recommendation_Agent reads this data and injects it into the model context.
- **Mood_Input**: Free-text describing the user's current state/vibe, which the Recommendation_Agent interprets in context as part of the conversation (no fixed numeric mood dimensions).
- **Time_Budget**: The time the user has available to play, inferred by the Recommendation_Agent from the conversation.
- **Owned_Platform**: A gaming platform the user has declared they own (e.g., Nintendo Switch 2, Xbox Series S, PSP).
- **Platform_List**: The user-configurable, extensible collection of Owned_Platform entries stored in the Memory_Store.
- **Community_Review**: Aggregated community sentiment, rating, or review score for a game, retrieved via Tavily_Enrichment.

## Requirements

### Requirement 1: Agent-Driven Conversational Intake

**User Story:** As a gamer, I want to tell the agent what I feel like playing in my own words, so that I get a recommendation through a natural conversation instead of a fixed questionnaire.

#### Acceptance Criteria

1. WHEN the user sends a message, THE Recommendation_Agent SHALL interpret it as natural conversation and decide how to proceed, rather than routing it through a fixed mood-then-time prompt sequence.
2. THE Recommendation_Agent SHALL infer the user's mood and available time from the conversation when the user supplies them, and SHALL NOT require them as mandatory, separately-prompted steps.
3. WHERE the user states taste or genre preferences in free text, THE Recommendation_Agent SHALL honor them when selecting a recommendation.
4. IF information the Recommendation_Agent needs to make a sound recommendation is missing or ambiguous and it cannot reasonably proceed, THEN THE Recommendation_Agent SHALL ask a focused clarifying question rather than guessing.
5. WHILE within a single conversation, THE Recommendation_Agent SHALL treat follow-ups (e.g. "I already played it", "something shorter", "something else") as continuations that refine the request without re-asking what is already known.

### Requirement 2: Source Data Exploration and Data Contract Definition

**User Story:** As a developer, I want the Game_Record schema to be derived from a documented exploration of what each source actually exposes, so that the data contract is grounded in real source data rather than assumptions.

#### Acceptance Criteria

1. THE Data_Contract SHALL be derived from a documented exploration of the fields available from each Record_Source, covering the Gmail purchase-confirmation email structure per supported retailer and Tavily_Enrichment response fields.
2. THE Data_Contract SHALL define the Game_Record schema, specifying for each field its name, type, whether it is required or optional, and the permitted source/provenance values (`gmail`, `manual`, `enrichment`).
3. THE Data_Contract SHALL define a normalized title and platform key used for deduplication across sources.
4. WHERE a Record_Source exposes a field not represented in the Data_Contract, THE exploration documentation SHALL record that field and the decision to include or exclude it.

### Requirement 3: Game Record Sources

**User Story:** As a gamer, I want my owned games gathered automatically from my purchase emails, with manual entry as a backup, so that my library is built with minimal effort.

#### Acceptance Criteria

1. THE Recommendation_Agent SHALL assemble the user's library from interchangeable Record_Sources applied in priority order: Gmail purchase-confirmation emails first, and manual UI/CLI entry second.
2. WHEN the user connects Gmail, THE Recommendation_Agent SHALL authenticate using a read-only mail scope, restrict its search to purchase-confirmation messages from known gaming retailers, and produce a Game_Record with source `gmail` and purchase_date for each confirmed game.
3. THE Streamlit_UI SHALL allow the user to add a game by manual entry, producing a Game_Record with source `manual` and using Tavily_Enrichment autocomplete after at least 3 typed characters.
4. WHEN records from any Record_Source are produced, THE Recommendation_Agent SHALL deduplicate them against existing Game_Records by normalized title and platform before storing them in the Memory_Store.
5. WHERE a Record_Source is unavailable or not connected, THE Recommendation_Agent SHALL continue using the remaining Record_Sources, and manual entry SHALL remain available at all times.

### Requirement 4: Privacy of Email-Sourced Records

**User Story:** As a privacy-conscious user, I want the app to read my purchase emails with least privilege and keep only what it needs, so that my mailbox content stays private.

#### Acceptance Criteria

1. THE Recommendation_Agent SHALL request no Gmail permission broader than read-only access.
2. WHEN extracting a Game_Record from a purchase-confirmation email, THE Recommendation_Agent SHALL retain only the Data_Contract fields (such as title, platform, and purchase_date) and SHALL discard raw email content.
3. THE Recommendation_Agent SHALL restrict its Gmail search to purchase-confirmation messages from known gaming retailers and SHALL leave unrelated email unread.

### Requirement 5: Metadata Enrichment via Tavily

**User Story:** As a gamer, I want every game enriched with accurate online data, so that recommendations reflect genre, playtime, platform availability, and how the community rates each title.

#### Acceptance Criteria

1. WHEN a Game_Record lacks enrichment metadata, THE Tavily_Enrichment SHALL query the Tavily API to populate genre, estimated_playtime, platform availability, and Community_Review, regardless of which Record_Source produced the record.
2. WHEN enrichment results are retrieved, THE Recommendation_Agent SHALL store the enriched fields in the Game_Record within the Memory_Store.
3. THE Recommendation_Agent SHALL exclude from recommendations any Game_Record whose platform availability does not intersect the Platform_List.
4. THE Tavily_Enrichment SHALL operate within the free-tier rate limits of the Tavily API.
5. IF Tavily_Enrichment cannot retrieve a field for a Game_Record, THEN THE Recommendation_Agent SHALL proceed with available information and indicate that the data is incomplete.

### Requirement 6: Platform Ownership Management

**User Story:** As a gamer, I want to manage which platforms I own, so that the agent only recommends games I can actually play.

#### Acceptance Criteria

1. THE Streamlit_UI SHALL allow the user to add, edit, and remove Owned_Platform entries in the Platform_List.
2. WHEN the user changes the Platform_List, THE Recommendation_Agent SHALL persist the updated Platform_List in the Memory_Store.
3. WHEN a session starts for a returning user, THE Recommendation_Agent SHALL retrieve the Platform_List from the Memory_Store.
4. THE Platform_List SHALL support additional platforms without requiring code changes to add a platform entry.
5. IF the Platform_List is empty when a recommendation is requested, THEN THE Recommendation_Agent SHALL prompt the user to add at least one Owned_Platform before generating a recommendation.

### Requirement 7: Personalized Game Recommendation

**User Story:** As a gamer, I want a single strong recommendation for a new game that reflects what I actually asked for, with clear reasoning and alternatives, so that I can quickly decide what to buy and play next.

#### Acceptance Criteria

1. WHEN the Recommendation_Agent has enough context (the user's request including any stated taste/genre, the owned library, and the Platform_List), THE Recommendation_Agent SHALL generate one primary recommendation for a game the user does NOT already own, available on at least one Owned_Platform, that reflects the user's mood, available time, and stated taste/genre.
2. THE Recommendation_Agent SHALL use the owned library to infer the user's taste and SHALL NOT recommend any game already present in the user's library; it SHALL select the recommendation itself — reasoning over the user's taste, its own knowledge of titles, and enrichment/web lookups — favoring well-regarded titles while honoring the stated preferences.
3. THE Recommendation_Agent SHALL present detailed reasoning for the primary recommendation, including a summary of its Community_Review when available, explaining the match to mood, available time, taste, and owned platforms, and noting that the title is not already in the user's library.
4. THE Recommendation_Agent SHALL offer up to 3 additional alternatives, each a game the user does not own and available on at least one Owned_Platform, with brief reasoning.
5. IF a candidate's platform availability or Community_Review cannot be confirmed, THEN THE Recommendation_Agent SHALL confirm it via web search or exclude it from the primary recommendation, or indicate the missing information when presenting it as an alternative.
6. WHEN matching a recommendation's platform availability to the Platform_List, THE Recommendation_Agent SHALL match at the platform-family level (e.g. owning "Xbox" covers "Xbox Series X/S" and "Xbox One"; "Switch" covers "Nintendo Switch") rather than requiring exact string equality.

### Requirement 8: Persistent Memory and Personalization

**User Story:** As a returning user, I want the agent to remember my history, so that recommendations improve over time and avoid repeats.

#### Acceptance Criteria

1. WHEN a recommendation session completes, THE Recommendation_Agent SHALL store the recommendation, user feedback, mood pattern, and session context in the Memory_Store.
2. WHEN a session starts for a returning user, THE Recommendation_Agent SHALL retrieve past session data and stored Game_Records from the Memory_Store and inject them into the model context to inform recommendations.
3. THE Recommendation_Agent SHALL avoid recommending a game recommended in the past 5 sessions unless the user explicitly requests a re-recommendation.
4. WHILE the user interacts across sessions, THE Recommendation_Agent SHALL refine its understanding of preferences based on accumulated mood patterns and feedback retrieved from the Memory_Store.

### Requirement 9: Retro Arcade UI with Chat and Library Views

**User Story:** As a user, I want a retro arcade themed interface with both a chat and a library dashboard on any device, so that getting recommendations and managing my games feels fun and easy.

#### Acceptance Criteria

1. THE Streamlit_UI SHALL apply a retro arcade machine visual theme, including retro-style fonts, an arcade cabinet aesthetic, and neon or CRT-style styling.
2. THE Streamlit_UI SHALL render responsively and remain fully usable on both desktop and mobile (phone) screen sizes while preserving the retro arcade theme — chat input, recommendation cards, the library view, and platform/game management SHALL all be operable on a phone.
3. THE Streamlit_UI SHALL present a conversational chat interface that displays the primary recommendation with its reasoning in a visually distinct card and shows alternatives in an expandable section.
4. THE Streamlit_UI SHALL provide a library/dashboard view presenting the user's platforms, their Game_Records grouped and filterable by platform, and their recommendation history.
5. THE Streamlit_UI SHALL allow the user to add a game, edit its Game_Record fields, and manage platforms directly in the library/dashboard view, writing to the same Game_Record store used by all Record_Sources.
6. WHERE Gmail is configured, THE Streamlit_UI SHALL provide controls to trigger an email import and report the number of games imported; WHERE Gmail is not configured (e.g. the hosted deployment), THE Streamlit_UI SHALL omit those controls and rely on the existing library and manual entry.

### Requirement 10: Error Handling and Graceful Degradation

**User Story:** As a user, I want the app to handle service failures gracefully, so that my experience is not disrupted.

#### Acceptance Criteria

1. WHEN any external service returns an error, THE Streamlit_UI SHALL display a user-friendly message without exposing technical details.
2. IF the Bedrock base model that powers the Recommendation_Agent is unavailable or the model call fails, THEN THE system SHALL surface a clear error and SHALL NOT substitute mock or fabricated recommendation content.
3. IF the Memory_Store is unavailable, THEN THE Recommendation_Agent SHALL operate statelessly for the current session and inform the user that personalization is temporarily limited.
4. IF Tavily_Enrichment is unavailable, THEN THE Recommendation_Agent SHALL recommend using existing Game_Records, user input, and the Platform_List, and inform the user that platform availability and community ratings could not be verified.
5. IF a Record_Source fails to authenticate or retrieve data, THEN THE Streamlit_UI SHALL display a sanitized message and SHALL continue operating using the remaining Record_Sources, with manual entry available as a fallback.

### Requirement 11: Agent Selects and Acts via Tools

**User Story:** As a gamer, I want the agent to take the right actions on my behalf during the conversation, so that it can manage my platforms and library, look things up, and persist results without me driving each step.

#### Acceptance Criteria

1. THE Recommendation_Agent SHALL act through a defined set of Tools to read and modify the Platform_List, read and update the Game_Record library, import from Record_Sources, enrich records, search the web, retrieve recent recommendations, and persist completed sessions.
2. THE Recommendation_Agent SHALL decide which Tools to call and in what order, and SHALL ask the user for missing information only when it cannot otherwise proceed.
3. WHEN a Tool reports an error or returns no data, THE Recommendation_Agent SHALL adapt and continue the conversation, surfacing only sanitized information to the user (Req 10) and never fabricating ratings, platforms, or titles the user does not own.
4. THE set of Tools SHALL be extensible — adding or removing a Tool SHALL NOT require changing the agent conversation loop.
5. WHILE running the tool-use loop, THE Agent_Runtime SHALL bound the number of tool-call rounds per user turn so the loop always terminates with either a final answer or a clear fallback message.

### Requirement 12: Hosted Deployment (Streamlit Community Cloud)

**User Story:** As the owner, I want the app hosted so I can use it from my phone, while keeping it private to me and protecting my AWS/Bedrock spend and data.

#### Acceptance Criteria

1. THE Streamlit_UI SHALL be deployable to Streamlit Community Cloud from the `cpav/gamegusto` GitHub repository via a single Streamlit entry point.
2. THE deployment SHALL be configured as a **private** Streamlit app whose viewer access is restricted to the owner's allow-listed Google account(s); the app SHALL NOT be openly usable by anyone with the URL.
3. THE application SHALL read all credentials (AWS region/keys for Bedrock + DynamoDB, Tavily key) from the platform's secrets manager / environment, never from the repository; no secret SHALL be committed.
4. THE hosted deployment SHALL operate without Gmail (the Gmail token stays local); it SHALL rely on the existing DynamoDB library and manual entry, degrading gracefully per Req 3.6 / 9.6.
5. THE runtime dependencies and Python version SHALL be pinned so the hosted build is reproducible.
6. THE hosted app SHALL be fully usable from a phone browser (Req 9.2).
