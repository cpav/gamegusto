/** Typed client for the GameGusto API (see `api/app.py`). */

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
  dedup_key: string;
  is_enriched: boolean;
}

export interface Platform {
  platform_id: string;
  name: string;
}

export type Verdict = "loved" | "not_for_me" | null;

export interface Pick {
  game_title: string;
  reasoning: string;
  estimated_playtime: number | null;
  verdict: Verdict;
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: init?.body ? { "Content-Type": "application/json" } : undefined,
    });
  } catch {
    throw new ApiError("Can't reach GameGusto — check that the API is running.");
  }
  if (!response.ok) {
    throw new ApiError(`Request failed (${response.status}).`);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const api = {
  library: () => request<{ records: GameRecord[]; memory_available: boolean }>("/api/library"),
  addGame: (title: string, platform?: string) =>
    request<{ record: GameRecord }>("/api/library", {
      method: "POST",
      body: JSON.stringify({ title, platform: platform ?? null }),
    }),
  setPlatform: (key: string, platform: string) =>
    request<{ record: GameRecord }>(`/api/library/${encodeURIComponent(key)}/platform`, {
      method: "PUT",
      body: JSON.stringify({ platform }),
    }),
  enrich: (key: string) =>
    request<{ record: GameRecord }>(`/api/library/${encodeURIComponent(key)}/enrich`, {
      method: "POST",
    }),
  removeGame: (key: string) =>
    request<void>(`/api/library/${encodeURIComponent(key)}`, { method: "DELETE" }),
  autocomplete: (q: string) =>
    request<{ suggestions: string[] }>(`/api/autocomplete?q=${encodeURIComponent(q)}`),

  platforms: () => request<{ platforms: Platform[] }>("/api/platforms"),
  addPlatform: (name: string) =>
    request<{ platform: Platform }>("/api/platforms", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  removePlatform: (id: string) =>
    request<void>(`/api/platforms/${encodeURIComponent(id)}`, { method: "DELETE" }),

  picks: () => request<{ picks: Pick[] }>("/api/picks"),
  setFeedback: (title: string, verdict: Verdict) =>
    request<unknown>("/api/picks/feedback", {
      method: "POST",
      body: JSON.stringify({ title, verdict }),
    }),
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
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
    signal,
  });
  if (response.status === 409) {
    onEvent({ kind: "error", message: "Still working on the previous message." });
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
