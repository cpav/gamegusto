# Requirements Document

## Introduction

A Python application that recommends the next video game to play based on the user's current mood, available time, gaming taste, and the platforms the user owns. A conversational agent powered by AWS Bedrock AgentCore interprets the user's mood and time, draws on a personal game library assembled from multiple sources, and returns one strong recommendation with clear reasoning plus optional alternatives. The user's library is built from interchangeable record sources — external platform APIs (e.g. Xbox), read-only Gmail purchase-confirmation emails (valuable for Nintendo, which has no history API), and manual entry through the UI — all normalized into a single canonical Game_Record. Every record is enriched via Tavily (genre, playtime, platform availability, community review) and persisted in AgentCore Memory so recommendations favor well-regarded titles playable on hardware the user owns and improve across sessions. The UI is built with Streamlit, styled as a retro arcade machine, and provides both a conversational chat and a library/dashboard view on desktop and mobile.

## Glossary

- **Recommendation_Agent**: The conversational AI agent powered by AWS Bedrock AgentCore that interprets user input, manages context, and generates game recommendations.
- **Streamlit_UI**: The web interface built with Streamlit, providing the chat and library/dashboard views on desktop and mobile.
- **Game_Record**: The single canonical record for one owned game, populated by every record source and used everywhere in the system. Fields include at minimum: title, platform(s), source (one of `xbox`, `gmail`, `manual`, `enrichment`), optional purchase_date, genre, estimated_playtime, community_review, and source/external identifiers. The exact field set is fixed by the Data_Contract.
- **Data_Contract**: The documented, versioned definition of the Game_Record schema (field names, types, required vs. optional, provenance values) that every record source and consumer conforms to.
- **Record_Source**: Any interchangeable origin of Game_Records — the Xbox platform API, Gmail purchase-confirmation emails, or manual UI entry — each of which produces records conforming to the Data_Contract.
- **Tavily_Enrichment**: The Tavily API integration used to populate Game_Record metadata (genre, estimated_playtime, platform availability, community_review) and to power autocomplete for manual entry.
- **AgentCore_Memory**: The persistent memory service that stores Game_Records, past recommendations, mood patterns, owned platforms, and preferences across sessions.
- **Mood_Input**: Free-text input describing the user's current emotional state, interpreted by the Recommendation_Agent into internal mood dimensions.
- **Time_Budget**: The time the user has available to play, parsed into a numeric duration in minutes.
- **Owned_Platform**: A gaming platform the user has declared they own (e.g., Nintendo Switch 2, Xbox Series S, PSP).
- **Platform_List**: The user-configurable, extensible collection of Owned_Platform entries stored in AgentCore_Memory.
- **Community_Review**: Aggregated community sentiment, rating, or review score for a game, retrieved via Tavily_Enrichment.

## Requirements

### Requirement 1: Conversational Mood and Time Intake

**User Story:** As a gamer, I want the agent to ask how I feel and how much time I have, so that recommendations match my current mood and fit my available window.

#### Acceptance Criteria

1. WHEN a recommendation session starts, THE Recommendation_Agent SHALL prompt the user with a conversational mood question.
2. WHEN the user provides a Mood_Input, THE Recommendation_Agent SHALL interpret the text and map it to internal mood dimensions.
3. IF the Recommendation_Agent cannot interpret the Mood_Input, THEN THE Recommendation_Agent SHALL ask a clarifying follow-up question.
4. WHEN the Recommendation_Agent has obtained a Mood_Input, THE Recommendation_Agent SHALL ask the user how much time they have available to play.
5. WHEN the user states their available time, THE Recommendation_Agent SHALL parse the response into a Time_Budget in minutes.
6. IF the stated available time is ambiguous, THEN THE Recommendation_Agent SHALL ask the user for a more specific estimate.

### Requirement 2: Source Data Exploration and Data Contract Definition

**User Story:** As a developer, I want the Game_Record schema to be derived from a documented exploration of what each source actually exposes, so that the data contract is grounded in real source data rather than assumptions.

#### Acceptance Criteria

1. THE Data_Contract SHALL be derived from a documented exploration of the fields available from each Record_Source, covering the Xbox platform API, Gmail purchase-confirmation email structure per supported retailer, and Tavily_Enrichment response fields.
2. THE Data_Contract SHALL define the Game_Record schema, specifying for each field its name, type, whether it is required or optional, and the permitted source/provenance values (`xbox`, `gmail`, `manual`, `enrichment`).
3. THE Data_Contract SHALL define a normalized title and platform key used for deduplication across sources.
4. WHERE a Record_Source exposes a field not represented in the Data_Contract, THE exploration documentation SHALL record that field and the decision to include or exclude it.

### Requirement 3: Game Record Sources

**User Story:** As a gamer, I want my owned games gathered automatically from my platforms and purchase emails, with manual entry as a backup, so that my library is built with minimal effort.

#### Acceptance Criteria

1. THE Recommendation_Agent SHALL assemble the user's library from interchangeable Record_Sources applied in priority order: external platform APIs first, Gmail purchase-confirmation emails second, and manual UI entry third.
2. WHEN the user connects an external platform account, THE Recommendation_Agent SHALL authenticate to that platform API and produce a Game_Record conforming to the Data_Contract for each retrieved game, populating all available metadata with source `xbox`.
3. WHEN the user connects Gmail, THE Recommendation_Agent SHALL authenticate using a read-only mail scope, restrict its search to purchase-confirmation messages from known gaming retailers, and produce a Game_Record with source `gmail` and purchase_date for each confirmed game.
4. THE Streamlit_UI SHALL allow the user to add a game by manual entry, producing a Game_Record with source `manual` and using Tavily_Enrichment autocomplete after at least 3 typed characters.
5. WHEN records from any Record_Source are produced, THE Recommendation_Agent SHALL deduplicate them against existing Game_Records by normalized title and platform before storing them in AgentCore_Memory.
6. WHERE a Record_Source is unavailable or not connected, THE Recommendation_Agent SHALL continue using the remaining Record_Sources, and manual entry SHALL remain available at all times.

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
2. WHEN enrichment results are retrieved, THE Recommendation_Agent SHALL store the enriched fields in the Game_Record within AgentCore_Memory.
3. THE Recommendation_Agent SHALL exclude from recommendations any Game_Record whose platform availability does not intersect the Platform_List.
4. THE Tavily_Enrichment SHALL operate within the free-tier rate limits of the Tavily API.
5. IF Tavily_Enrichment cannot retrieve a field for a Game_Record, THEN THE Recommendation_Agent SHALL proceed with available information and indicate that the data is incomplete.

### Requirement 6: Platform Ownership Management

**User Story:** As a gamer, I want to manage which platforms I own, so that the agent only recommends games I can actually play.

#### Acceptance Criteria

1. THE Streamlit_UI SHALL allow the user to add, edit, and remove Owned_Platform entries in the Platform_List.
2. WHEN the user changes the Platform_List, THE Recommendation_Agent SHALL persist the updated Platform_List in AgentCore_Memory.
3. WHEN a session starts for a returning user, THE Recommendation_Agent SHALL retrieve the Platform_List from AgentCore_Memory.
4. THE Platform_List SHALL support additional platforms without requiring code changes to add a platform entry.
5. IF the Platform_List is empty when a recommendation is requested, THEN THE Recommendation_Agent SHALL prompt the user to add at least one Owned_Platform before generating a recommendation.

### Requirement 7: Personalized Game Recommendation

**User Story:** As a gamer, I want a single strong recommendation with clear reasoning and optional alternatives, so that I can quickly decide what to play next.

#### Acceptance Criteria

1. WHEN the Recommendation_Agent has the Mood_Input, Time_Budget, library Game_Records, and Platform_List, THE Recommendation_Agent SHALL generate one primary recommendation available on at least one Owned_Platform and fitting within the Time_Budget.
2. THE Recommendation_Agent SHALL prioritize Game_Records with higher Community_Review quality among candidates that match mood, Time_Budget, and Owned_Platform constraints.
3. THE Recommendation_Agent SHALL present detailed reasoning for the primary recommendation, including a summary of its Community_Review, explaining the match to mood, available time, taste, and owned platforms.
4. WHEN the user requests alternatives, THE Recommendation_Agent SHALL provide up to 3 additional recommendations, each available on at least one Owned_Platform, with brief reasoning.
5. IF a candidate's platform availability or Community_Review cannot be confirmed, THEN THE Recommendation_Agent SHALL exclude it from the primary recommendation or indicate the missing information when presenting it as an alternative.

### Requirement 8: Persistent Memory and Personalization

**User Story:** As a returning user, I want the agent to remember my history, so that recommendations improve over time and avoid repeats.

#### Acceptance Criteria

1. WHEN a recommendation session completes, THE Recommendation_Agent SHALL store the recommendation, user feedback, mood pattern, and session context in AgentCore_Memory.
2. WHEN a session starts for a returning user, THE Recommendation_Agent SHALL retrieve past session data from AgentCore_Memory to inform recommendations.
3. THE Recommendation_Agent SHALL avoid recommending a game recommended in the past 5 sessions unless the user explicitly requests a re-recommendation.
4. WHILE the user interacts across sessions, THE Recommendation_Agent SHALL refine its understanding of preferences based on accumulated mood patterns and feedback.

### Requirement 9: Retro Arcade UI with Chat and Library Views

**User Story:** As a user, I want a retro arcade themed interface with both a chat and a library dashboard on any device, so that getting recommendations and managing my games feels fun and easy.

#### Acceptance Criteria

1. THE Streamlit_UI SHALL apply a retro arcade machine visual theme, including retro-style fonts, an arcade cabinet aesthetic, and neon or CRT-style styling.
2. THE Streamlit_UI SHALL render responsively on desktop and mobile screen sizes while preserving the retro arcade theme.
3. THE Streamlit_UI SHALL present a conversational chat interface that displays the primary recommendation with its reasoning in a visually distinct card and shows alternatives in an expandable section.
4. THE Streamlit_UI SHALL provide a library/dashboard view presenting the user's platforms, their Game_Records grouped and filterable by platform, and their recommendation history.
5. THE Streamlit_UI SHALL allow the user to add a game, edit its Game_Record fields, and manage platforms directly in the library/dashboard view, writing to the same Game_Record store used by all Record_Sources.
6. THE Streamlit_UI SHALL provide controls to connect an external platform account, connect Gmail and trigger an email import, and report the number of games imported.

### Requirement 10: Error Handling and Graceful Degradation

**User Story:** As a user, I want the app to handle service failures gracefully, so that my experience is not disrupted.

#### Acceptance Criteria

1. WHEN any external service returns an error, THE Streamlit_UI SHALL display a user-friendly message without exposing technical details.
2. IF AgentCore_Memory is unavailable, THEN THE Recommendation_Agent SHALL operate statelessly for the current session and inform the user that personalization is temporarily limited.
3. IF Tavily_Enrichment is unavailable, THEN THE Recommendation_Agent SHALL recommend using existing Game_Records, user input, and the Platform_List, and inform the user that platform availability and community ratings could not be verified.
4. IF a Record_Source fails to authenticate or retrieve data, THEN THE Streamlit_UI SHALL display a sanitized message and SHALL continue operating using the remaining Record_Sources, with manual entry available as a fallback.
