# GameGusto web client (v2)

The installable PWA that replaces the Streamlit UI: chat with live streaming,
and a console-store library. Talks only to the HTTP API (`api/`) — no Python
imports, so the same client can later be wrapped natively.

Requires Node 20+ (this repo was developed against 22 LTS).

## Run it

Two processes. The Vite dev server proxies `/api` to port 8000, so the client
is same-origin and SSE needs no CORS negotiation.

```bash
# 1. an API on :8000 — either the real one…
uvicorn --factory api.main:build --reload --port 8000     # needs AWS + Tavily

#    …or the offline mock (no credentials, no Bedrock spend)
python scripts/mock_api.py

# 2. the client
cd web && npm install && npm run dev                       # http://localhost:5173
```

`npm run build` type-checks and bundles to `dist/`; `npm run typecheck` alone
is the fast gate.

## Install it on a device

Open the served URL and use **Add to Home Screen** (iOS) or **Install** (Chrome,
macOS Safari "Add to Dock"). The manifest runs it standalone with the Panel
icon and no browser chrome.

## Structure

| Path | What it is |
|---|---|
| `src/api.ts` | Typed API client. Chat is SSE-over-POST, so it parses the `event:`/`data:` framing itself (`EventSource` is GET-only). |
| `src/App.tsx` | Shell: marquee, tab bar, theme toggle. Both views stay mounted so switching never interrupts a streaming answer. |
| `src/components/ChatView.tsx` | Streaming conversation: tool chips, live token deltas, quick replies, per-turn cost. |
| `src/components/LibraryView.tsx` | Card grid, instant client-side search, filter chips, recent picks. |
| `src/components/GameSheet.tsx` | Detail bottom sheet — the actions Streamlit hid behind `⋯`. |
| `src/components/AddGameSheet.tsx` | Add-a-game with live autocomplete. |
| `src/markdown.tsx` | Small renderer for the agent's markdown. No `dangerouslySetInnerHTML`, so a reply can't inject markup. |
| `src/styles/index.css` | All styling. Imports `design/tokens.css` at the repo root — the tokens are never copied here, so the app and the design spec can't drift. |

## Design

The approved identity ("The Blend" + the Panel logo) is specified in
[`../design/README.md`](../design/README.md). Colors are used strictly by role:
**thrill** (pink) for send/kickers, **charge** (cyan) for anything interactive,
**warmth** (amber) for the wordmark, lamps, prices and scores. Dark and light
are one token set with two grounds — never a second design. The Panel mark is
the only logo and never varies by context.
