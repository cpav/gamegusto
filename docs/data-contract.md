# GameGusto Data Contract

**Contract version:** `2.0.0`
**Status:** Locked
**Last updated:** 2026-06-14
**Owns:** `models/game_record.py` (`GameRecord`, `CommunityReview`)

> Satisfies Requirement 2 (Source Data Exploration and Data Contract Definition).
> This document is the single source of truth for the canonical `GameRecord`
> schema. Every Record_Source (Gmail, manual) produces records conforming
> to this contract, and every consumer (LibraryService, Recommender, MemoryService,
> UI) reads it. There are no per-source record types.

## 1. Purpose and scope

Requirement 2.1 mandates that the `GameRecord` schema be **derived from a
documented exploration of what each source actually exposes**, not from
assumptions. This document records that exploration:

- Section 3 — the **Gmail purchase-confirmation email** structure per supported
  retailer (Nintendo eShop, Microsoft Store).
- Section 4 — the **Tavily** enrichment response fields.

For every exposed field, the exploration records an explicit **include / exclude**
decision (Req 2.4). Section 5 defines the normalized **dedup key** (Req 2.3) and
Section 6 locks the versioned `GameRecord` contract (Req 2.2).

### 1.1 Persistence (storage-agnostic)

The contract describes the canonical in-memory `GameRecord` shape only; it is
**storage-agnostic**. In production these records are **persisted in a DynamoDB
table using a single-table design**, written and read through the
`MemoryService` / `DynamoDBMemoryClient` boundary. DynamoDB does not accept
native floating-point numbers, so the persistence layer performs
`float`↔`Decimal` conversion for numeric fields (e.g. `community_review.score`)
at the read/write boundary. That conversion is an implementation detail of the
persistence layer and is **not part of this contract** — consumers always see
the Python types declared in Section 6.

## 2. Exploration methodology and caveats

This is a deliberate discovery spike that *precedes* locking the contract. Field
inventories below were assembled from the official/public documentation and the
observable response shapes of each source:

- **Gmail** — the Gmail REST API (`users.messages.list` / `users.messages.get`)
  under the `gmail.readonly` scope, inspecting real purchase-confirmation message
  headers and bodies from the two supported retailers.
- **Tavily** — the Tavily Search API response envelope used for enrichment.

Caveat: these are external, third-party surfaces that can change without notice.
Where a field's availability is unreliable, that is noted and the field is either
excluded or made optional. The contract intentionally keeps required fields to the
minimum every source can guarantee.

## 3. Gmail purchase-confirmation emails — exposed fields

Source provenance value: `source = "gmail"` (Req 3.3). Access is **read-only**
(`gmail.readonly`, Req 4.1) and the search is **restricted to known retailer
senders** (Req 3.3, 4.3). Per Req 4.2, only contract fields are retained; **raw
email content is discarded** immediately after parsing.

### 3.1 Gmail envelope fields (common)

| Exposed field | Type (raw) | Description | Decision | Mapped to contract | Rationale |
|---|---|---|---|---|---|
| `id` / `threadId` | string | Gmail message/thread id | **Exclude** | — | Mailbox-internal; retaining it would tie records to raw email (violates Req 4.2). |
| `payload.headers[From]` | string | Sender address | **Exclude (use-then-discard)** | — | Used only to confirm the message is from a known retailer; not stored. |
| `payload.headers[Subject]` | string | Email subject | **Exclude (use-then-discard)** | — | Parsed to extract title; the subject itself is not stored. |
| `payload.headers[Date]` | RFC2822 datetime | Email date | **Include (transformed)** | `purchase_date` (date only) | Best available proxy for purchase date; reduced to a `date` (no time/timezone retained). |
| `payload.body` / `payload.parts` | base64 text/html | Email body | **Exclude (use-then-discard)** | — | Parsed for title/platform line items, then discarded (Req 4.2). |
| `snippet` | string | Body preview | **Exclude** | — | Raw content; never stored. |
| `labelIds` | list[string] | Gmail labels | **Exclude** | — | Mailbox metadata; irrelevant. |

### 3.2 Nintendo eShop confirmation (`sender_id = "nintendo"`)

Known sender: `no-reply@accounts.nintendo.com`. Two kinds of mail arrive from this
sender: **purchase receipts** ("Thank you for your Nintendo eShop purchase") and
**Transaction Statements** (funds-added receipts). Only purchase receipts carry a
game; statements have no `Item` section and are skipped.

| Information present in email | Where it appears | Decision | Mapped to contract | Rationale |
|---|---|---|---|---|
| Purchased game title | Body `Item` section: an `Item` header, a quantity line (e.g. `1x`), then the product name | **Include** | `title` | Required key material. Parsed from the Item section, not the subject. |
| Platform | Body "Device Type" (e.g. "Nintendo Switch / Nintendo Switch 2") | **Include** | `platforms = ["Nintendo Switch"]` | eShop purchases are Switch-family; parser sets the platform. |
| Purchase/order date | Email `Date` header | **Include** | `purchase_date` | Best available proxy; reduced to a `date`. |
| Transaction/receipt number, price, account email | Body | **Exclude** | — | Financial/PII; not contract fields (Req 4.2). |

### 3.3 Microsoft order confirmation (`sender_id = "microsoft_store"`)

Known sender: `microsoft-noreply@microsoft.com` (subject "Your Microsoft order #… has
been processed"). A single order may contain **multiple line items**, so the parser
returns one `GameRecord` per item.

| Information present in email | Where it appears | Decision | Mapped to contract | Rationale |
|---|---|---|---|---|
| Purchased item title(s) | Body "Order details" pipe-delimited table: `\| <title> \| <qty> \| <price> \|` | **Include** | `title` (one record per row) | Required key material. Header and publisher ("By: …") rows are skipped. |
| Platform / device | Body tokens ("Xbox", "Windows"/"PC") when present | **Include** | `platforms` (Xbox token → `"Xbox Series X/S"`, Windows/PC → `"PC"`; default `["Xbox Series X/S"]`) | Establishes the owned console; family-aware matching covers older "Xbox One"/bare "Xbox" availability. |
| Purchase/order date | Email `Date` header | **Include** | `purchase_date` | Req 3.2. |
| Order number, price, publisher, account ids | Body | **Exclude** | — | Order metadata / PII; not contract fields. |

**Retailer notes**

- The retailer→parser mapping is extensible: adding a retailer means adding a known
  sender and a parser, with no contract change.
- **PlayStation** was probed during exploration but the reference mailbox contained
  no PlayStation purchase receipts (only marketing mail), so no PlayStation parser is
  defined yet; it can be added when a real receipt format is available.
- Genre, playtime, platform availability, and community review are **not** present in
  purchase emails — they are enrichment fields (Tavily, Req 5.1).

## 4. Tavily enrichment — exposed fields

Tavily-populated fields carry `source = "enrichment"` when a record originates
purely from enrichment; when enriching an existing source record, enrichment
fills the optional fields without changing the record's original `source`.

Tavily returns a search envelope. We do **not** keyword-parse it directly;
instead `agent.enricher.Enricher` feeds the `answer` + `results[].content`
snippets to the Bedrock model, which (using its own knowledge of the title plus
the snippets) returns a structured JSON classification — `genre`,
`estimated_playtime_minutes` (main-story completion time), `platform_availability`,
and `community_review {score, summary}`. This reliably classifies titles that
keyword matching mislabels (e.g. Metal Slug as a run-and-gun shooter, not
"Puzzle"). The Tavily fields below are the raw inputs to that step:

| Exposed field | Type (raw) | Description | Decision | Mapped to contract | Rationale |
|---|---|---|---|---|---|
| `query` | string | Echo of the search query | **Exclude** | — | Diagnostic only. |
| `answer` | string | LLM-synthesized answer | **Include (to LLM)** | feeds the model's `genre`, `estimated_playtime`, `community_review` classification | Primary snippet fed to the enrichment model. |
| `results[].title` | string | Result page title | **Include (parsed)** | autocomplete suggestions; cross-check | Used for manual-entry autocomplete (Req 3.4) and to corroborate the game title. |
| `results[].url` | string | Source URL | **Exclude** | — | Provenance only; not a contract field. |
| `results[].content` | string | Snippet text | **Include (parsed)** | `community_review.sentiment_summary`, `platform_availability` | Mined for platform availability and review sentiment. |
| `results[].score` | float | Tavily relevance score | **Exclude** | — | Search relevance, not a game review score. Must not be confused with `community_review.score`. |
| `images` | list | Optional images | **Exclude** | — | Presentation-only. |
| `response_time` | float | API latency | **Exclude** | — | Diagnostic only. |
| — (LLM) genre | — | Model-classified from snippets + own knowledge | **Include** | `genre` | Req 5.1. |
| — (LLM) estimated playtime | — | Model-estimated main-story completion, in minutes | **Include** | `estimated_playtime` (int, minutes) | Req 5.1. Completion time, not a session length — the agent reasons about session fit. |
| — (LLM) platform availability | — | Model-listed platforms | **Include** | `platform_availability` | Req 5.3 — drives the family-aware playable filter. |
| — (LLM) review score | — | Model-normalized 0.0–10.0 | **Include** | `community_review.score` | Req 7.2 ranking. |
| — (derived) review source count | — | Count of snippets fed to the model | **Include** | `community_review.source_count` | Confidence signal. |

**Tavily notes**

- Tavily fields are best-effort: any field that cannot be derived is left unset
  and the record is treated as incomplete (Req 5.5). `is_enriched()` gates
  cache-first enrichment.
- `results[].score` (search relevance) is explicitly **not** the community review
  score; only the derived, normalized review value populates
  `community_review.score`.

## 5. Normalized dedup key (Req 2.3)

Records from different sources must collapse to one when they describe the same
owned game. The dedup key is the **normalized title + first platform**:

```
dedup_key = f"{title.strip().casefold()}|{platform.strip().casefold()}"
```

where `platform` is `platforms[0]` if present, else the empty string.

Normalization rules:

- **`casefold()`** — case-insensitive, Unicode-aware (stronger than `lower()`),
  so `"HADES"`, `"Hades"`, and `"hades"` match.
- **`strip()`** — leading/trailing whitespace removed from both title and
  platform, so `"  Hades "` matches `"Hades"`.
- The `|` separator keeps title and platform segments unambiguous.

Dedup precedence (consumed by `LibraryService.refresh`, Req 3.5): sources run in
order **Gmail → manual**; the first record seen for a given `dedup_key`
wins and later duplicates are skipped. `MemoryService` applies the same key
defensively so duplicates are never persisted.

## 6. The locked `GameRecord` contract (Req 2.2)

Realized in code as `models/game_record.py`. **Locked at version 2.0.0** by this
exploration task; all sources and consumers conform to it from this point on.

### 6.1 `CommunityReview`

| Field | Type | Required | Provenance | Notes |
|---|---|---|---|---|
| `score` | `float` | yes | enrichment | Normalized 0.0–10.0. |
| `sentiment_summary` | `str` | yes | enrichment | Short summary used in recommendation reasoning (Req 7.3). |
| `source_count` | `int` | yes | enrichment | Number of aggregated sources. |

### 6.2 `GameRecord`

| Field | Type | Required | Default | Provenance | Notes |
|---|---|---|---|---|---|
| `title` | `str` | **yes** | — | gmail, manual | Canonical display/key title. |
| `platforms` | `list[str]` | no | `[]` | gmail, manual | Platforms the user owns the title on. First entry feeds the dedup key. |
| `source` | `Literal["gmail","manual","enrichment"]` | no | `"manual"` | — | Provenance; the only permitted values (Req 2.2). |
| `purchase_date` | `date \| None` | no | `None` | gmail | Set for Gmail imports (Req 3.3); `None` otherwise. |
| `genre` | `str \| None` | no | `None` | enrichment | Tavily (Req 5.1). |
| `estimated_playtime` | `int \| None` | no | `None` | enrichment | Minutes; Tavily (Req 5.1). Normalized to minutes to compare against the Time_Budget. |
| `community_review` | `CommunityReview \| None` | no | `None` | enrichment | Tavily (Req 5.1, 7.2). |
| `platform_availability` | `list[str]` | no | `[]` | enrichment | Platforms the game is available on (Req 5.3); drives the playable filter. |
| `external_ids` | `dict[str, str]` | no | `{}` | — | Reserved/optional. Currently **unused** (formerly held the Xbox `titleId`); retained for future source-specific IDs. Defaults to `{}`. |

### 6.3 Derived members

| Member | Kind | Definition | Requirement |
|---|---|---|---|
| `dedup_key` | property → `str` | `f"{title.strip().casefold()}\|{platform.strip().casefold()}"` where `platform = platforms[0]` or `""` | 2.3, 3.5 |
| `is_enriched()` | method → `bool` | `genre is not None and bool(platform_availability)` | 5.1 |

### 6.4 Permitted `source` / provenance values (Req 2.2)

| Value | Meaning |
|---|---|
| `gmail` | Extracted from a read-only Gmail purchase-confirmation email. |
| `manual` | Entered by the user through the library UI. |
| `enrichment` | Originated purely from enrichment (no owning source). |

### 6.5 Field provenance matrix

Which source can populate which field (✓ = populates, — = leaves at default):

| Field | gmail | manual | enrichment |
|---|---|---|---|
| `title` | ✓ | ✓ | — |
| `platforms` | ✓ | ✓ | — |
| `source` | ✓ | ✓ | ✓ |
| `purchase_date` | ✓ | optional | — |
| `genre` | — | optional | ✓ |
| `estimated_playtime` | — | optional | ✓ |
| `community_review` | — | — | ✓ |
| `platform_availability` | — | — | ✓ |
| `external_ids` | — | — | — |

## 7. Versioning policy

- This contract is **semantically versioned**. The current locked version is
  **`2.0.0`**.
- **Patch** (`2.0.x`): documentation clarifications, no schema change.
- **Minor** (`2.x.0`): backward-compatible additions (new optional field, new
  permitted `source` value, new retailer parser).
- **Major** (`x.0.0`): breaking changes — removing/renaming a field, changing a
  field's type or required-ness, removing a permitted `source` value, or changing
  the dedup key algorithm.
- Any change to the dedup key normalization (Section 5) or to `GameRecord`'s
  field set/types is a contract change and must bump the version here and in
  `models/game_record.py`, and re-validate the model unit tests (task 2.4).

### Version history

| Version | Change | Type |
|---|---|---|
| `1.0.0` | Initial locked contract (sources: Xbox, Gmail, manual; enrichment via Tavily). | — |
| `2.0.0` | Removed the `xbox` provenance value and the Xbox source exploration. Permitted `source` set narrowed to (`gmail`, `manual`, `enrichment`). This is a **MAJOR** bump because removing a permitted `source` value is a breaking change to the contract's permitted source set. | Major |

## 8. Traceability

| Requirement | Where satisfied |
|---|---|
| 2.1 — contract derived from documented source exploration | Sections 3, 4 |
| 2.2 — schema: names, types, required/optional, provenance values | Section 6 |
| 2.3 — normalized title+platform dedup key | Section 5, 6.3 |
| 2.4 — every exposed field has an include/exclude decision | Sections 3, 4 (decision columns) |
