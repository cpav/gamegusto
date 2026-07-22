/** Typed client for the GameGusto API (see `api/app.py`). */

import { AUTH_EXPIRED_EVENT, clearSession, currentToken, payloadHash } from "./auth";
import type { Course, TasteVerdict } from "./taste";

/**
 * Drop the dead session and tell the shell.
 *
 * Centralised so every path that can see a 401 behaves identically: the user
 * lands on the sign-in screen rather than on a view full of failed requests.
 */
function sessionExpired(): void {
  clearSession();
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
}

export interface CommunityReview {
  score: number;
  sentiment_summary: string;
  source_count: number;
}

export interface GameRecord {
  title: string;
  platforms: string[];
  source: string;
  purchase_date: string | null;
  genre: string | null;
  estimated_playtime_hours: number | null;
  community_review: CommunityReview | null;
  platform_availability: string[];
  external_ids: Record<string, string>;
  cover_url: string | null;
  taste: TasteVerdict | null;
  course: Course | null;
  taste_note: string | null;
  dedup_key: string;
  is_enriched: boolean;
}

export interface Platform {
  platform_id: string;
  name: string;
}

/** One IGDB add-game suggestion: enough to pick the right title and platform. */
export interface GameSuggestion {
  name: string;
  platforms: string[];
  cover_url: string | null;
}

export interface Pick {
  game_title: string;
  reasoning: string;
  estimated_playtime: number | null;
  owned: boolean;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  notes?: string[];
}

export interface Usage {
  inputTokens?: number;
  outputTokens?: number;
  cacheReadInputTokens?: number;
  cacheWriteInputTokens?: number;
}

/** One decoded server event from the chat stream. */
export type ChatEvent =
  | { kind: "delta"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "tool"; tool: string }
  | { kind: "text"; text: string }
  | { kind: "error"; message: string }
  | { kind: "done"; usage: Usage; memoryAvailable: boolean };

export class ApiError extends Error {}

/** Raised on 401 so the shell can send the user back to sign in. */
export class AuthExpiredError extends ApiError {}

/**
 * Headers every API call needs.
 *
 * Two things are going on, both forced by the deployment:
 *
 *  - The token travels as `X-Id-Token`, not `Authorization`. CloudFront's
 *    origin access control puts its own SigV4 signature in `Authorization`
 *    when signing requests to the Lambda function URL, so that header is not
 *    ours to use.
 *  - Any request with a body carries `x-amz-content-sha256`. Lambda refuses
 *    unsigned payloads behind OAC, and without this the request fails with a
 *    signature mismatch that points nowhere near the real cause.
 *
 * Both are centralised here so no call site can forget them.
 */
async function authHeaders(body?: BodyInit | null): Promise<Record<string, string>> {
  const headers: Record<string, string> = {};
  if (body) {
    headers["Content-Type"] = "application/json";
    headers["x-amz-content-sha256"] = await payloadHash(String(body));
  }
  const token = await currentToken();
  if (token) headers["X-Id-Token"] = token;
  return headers;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { ...(await authHeaders(init?.body)), ...(init?.headers as Record<string, string>) },
    });
  } catch {
    throw new ApiError("Can't reach GameGusto — check that the API is running.");
  }
  if (response.status === 401) {
    sessionExpired();
    throw new AuthExpiredError("Session expired.");
  }
  if (!response.ok) {
    // Include the API's own explanation when it sent one. Without this a 404
    // from a stale key and a 403 from the edge read identically to the user,
    // and to anyone they report it to.
    let detail = "";
    try {
      const body = (await response.clone().json()) as { detail?: string };
      if (body?.detail) detail = `: ${body.detail}`;
    } catch {
      /* not JSON — the status alone will have to do */
    }
    throw new ApiError(`${response.status}${detail}`);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

/**
 * Record-scoped actions send the dedup key in the BODY, never the URL.
 *
 * A dedup key is `title|platform`, and platforms like "Xbox Series X/S"
 * contain a slash. Percent-encoded into a path, CloudFront decodes %2F back
 * into a real separator, the path segment splits, and the API 404s on a game
 * that plainly exists — which is exactly what happened to every Xbox title in
 * the library.
 */
export const api = {
  library: () => request<{ records: GameRecord[]; memory_available: boolean }>("/api/library"),
  addGame: (title: string, platform?: string) =>
    request<{ record: GameRecord }>("/api/library", {
      method: "POST",
      body: JSON.stringify({ title, platform: platform ?? null }),
    }),
  setPlatform: (key: string, platform: string) =>
    request<{ record: GameRecord }>("/api/library/platform", {
      method: "PUT",
      body: JSON.stringify({ dedup_key: key, platform }),
    }),
  enrich: (key: string) =>
    request<{ record: GameRecord }>("/api/library/enrich", {
      method: "POST",
      body: JSON.stringify({ dedup_key: key }),
    }),
  /**
   * Enrich everything that still needs it, one bounded batch per call.
   *
   * The server caps the batch because each record costs a search and a model
   * call and the edge allows 60 seconds; `remaining` is how the caller knows
   * to go again. `refresh` re-does the whole library instead. See api/app.py.
   */
  enrichAll: (refresh = false, offset = 0) =>
    request<{ enriched: number; remaining: number; records: GameRecord[] }>(
      `/api/library/enrich-all?refresh=${refresh}&offset=${offset}`,
      { method: "POST" },
    ),
  removeGame: (key: string) =>
    request<void>("/api/library/remove", {
      method: "POST",
      body: JSON.stringify({ dedup_key: key }),
    }),
  /**
   * Live title suggestions from IGDB for the add-game box.
   *
   * Each result carries the platforms the game shipped on and its box art, so
   * the picker confirms the title and then chooses the platform they own it on.
   */
  catalogSearch: (q: string) =>
    request<{ results: GameSuggestion[] }>(`/api/catalog/search?q=${encodeURIComponent(q)}`),

  platforms: () => request<{ platforms: Platform[] }>("/api/platforms"),
  addPlatform: (name: string) =>
    request<{ platform: Platform }>("/api/platforms", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  removePlatform: (id: string) =>
    request<void>(`/api/platforms/${encodeURIComponent(id)}`, { method: "DELETE" }),

  /**
   * Rate an owned game — the user's own verdict, course, and note.
   *
   * The full intended state is always sent (a field left `null` is cleared), so
   * the server never has to merge. This is the taste the agent learns from.
   */
  setTaste: (
    key: string,
    taste: TasteVerdict | null,
    course: Course | null,
    note: string | null,
  ) =>
    request<{ record: GameRecord }>("/api/library/taste", {
      method: "PUT",
      body: JSON.stringify({ dedup_key: key, taste, course, note }),
    }),

  picks: () => request<{ picks: Pick[] }>("/api/picks"),
  clearPicks: () => request<void>("/api/picks", { method: "DELETE" }),

  conversation: () => request<{ messages: Message[] }>("/api/conversation"),
  resetConversation: () => request<void>("/api/conversation", { method: "DELETE" }),
};

/**
 * Stream one chat turn.
 *
 * SSE over POST, so `EventSource` (GET-only) can't be used — this reads the
 * response body and parses the `event:`/`data:` framing itself. Events are
 * handed to `onEvent` as they arrive.
 */
export async function streamChat(
  message: string,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const body = JSON.stringify({ message });
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: await authHeaders(body),
    body,
    signal,
  });
  if (response.status === 409) {
    onEvent({ kind: "error", message: "Still working on the previous message." });
    return;
  }
  if (response.status === 401) {
    sessionExpired();
    onEvent({ kind: "error", message: "Session expired — sign in again." });
    return;
  }
  if (!response.ok || !response.body) {
    onEvent({ kind: "error", message: "Can't reach GameGusto right now." });
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // Events are separated by a blank line; a partial tail stays buffered.
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = decodeEvent(block);
      if (event) onEvent(event);
    }
  }
}

function decodeEvent(block: string): ChatEvent | null {
  let name = "";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event: ")) name = line.slice(7).trim();
    else if (line.startsWith("data: ")) data = line.slice(6);
  }
  if (!name || !data) return null;
  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(data);
  } catch {
    return null;
  }
  switch (name) {
    case "delta":
      return { kind: "delta", text: String(payload.text ?? "") };
    case "thinking":
      return { kind: "thinking", text: String(payload.text ?? "") };
    case "tool":
      return { kind: "tool", tool: String(payload.tool ?? "") };
    case "text":
      return { kind: "text", text: String(payload.text ?? "") };
    case "error":
      return { kind: "error", message: String(payload.message ?? "Something went wrong.") };
    case "done":
      return {
        kind: "done",
        usage: (payload.usage ?? {}) as Usage,
        memoryAvailable: payload.memory_available !== false,
      };
    default:
      return null;
  }
}
