import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, streamChat, type ChatEvent, type Message, type Pick, type Usage, type Verdict } from "../api";
import { Logo } from "./Logo";
import { Markdown } from "../markdown";
import { RecCard } from "./RecCard";

/** Friendly transient label per tool, mirroring the Streamlit vocabulary. */
const TOOL_LABELS: Record<string, string> = {
  get_owned_platforms: "🎮 checking your platforms",
  add_platform: "🎮 adding a platform",
  remove_platform: "🎮 removing a platform",
  get_library: "📚 reading your library",
  add_manual_game: "📚 adding a game",
  set_game_fields: "📚 updating a game",
  import_gmail: "📧 importing purchases",
  enrich_game: "🔎 looking up game details",
  web_search: "🌐 searching the web",
  get_recent_recommendations: "🧠 recalling recent picks",
  save_recommendation: "💾 saving this pick",
};

const QUICK_REPLIES = [
  { label: "Something shorter", text: "Something shorter" },
  { label: "Already played it", text: "I already played that one" },
  { label: "Surprise me", text: "Surprise me with something different" },
];

const STARTERS = [
  { label: "🤔 Help me decide", text: "I'm not sure what I feel like — help me figure out what to play next" },
  { label: "🎯 Match my taste", text: "Recommend something new based on the taste in my library" },
  { label: "💸 Good deals", text: "What good games are on a deal right now for my platforms?" },
  { label: "⏱️ Short on time", text: "I've only got a short while tonight — what should I play?" },
];

/** Bedrock Sonnet $/1M tokens — advisory only; the bill is authoritative. */
const PRICE = { input: 3.0, output: 15.0, cacheRead: 0.3, cacheWrite: 3.75 };

function formatUsage(usage: Usage): string | null {
  const input = usage.inputTokens ?? 0;
  const output = usage.outputTokens ?? 0;
  const cacheRead = usage.cacheReadInputTokens ?? 0;
  const cacheWrite = usage.cacheWriteInputTokens ?? 0;
  const total = input + cacheRead + cacheWrite;
  if (!total && !output) return null;
  const cost =
    (input * PRICE.input +
      output * PRICE.output +
      cacheRead * PRICE.cacheRead +
      cacheWrite * PRICE.cacheWrite) /
    1_000_000;
  const cached = total ? Math.round((100 * cacheRead) / total) : 0;
  return `~$${cost.toFixed(2)} · ${(total / 1000).toFixed(1)}k in (${cached}% cached) · ${(output / 1000).toFixed(1)}k out`;
}

export function ChatView({
  onLibraryChanged,
  onUsage,
}: {
  onLibraryChanged: () => void;
  onUsage: (usage: string | null) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [liveText, setLiveText] = useState("");
  const [liveNotes, setLiveNotes] = useState<string[]>([]);
  const [activeTool, setActiveTool] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [usage, setUsage] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api
      .conversation()
      .then((data) => setMessages(data.messages))
      .catch(() => setError("Can't reach GameGusto — is the API running?"))
      .finally(() => setLoaded(true));
    void refreshPicks();
  }, []);

  async function refreshPicks() {
    const data = await api.picks().catch(() => null);
    if (data) setPicks(data.picks);
  }

  /**
   * The saved pick a reply is about, matched by title appearing in the text.
   * `save_recommendation` is what makes a turn a recommendation, so a reply
   * with no matching pick (a clarifying question) correctly gets no header.
   */
  function pickFor(text: string): Pick | undefined {
    // Only the opening paragraph counts. The agent leads with the pick it is
    // making, while later paragraphs routinely name OTHER games ("unlike Hades,
    // which you own…") — matching on the whole reply would put a
    // "Tonight's pick" header on a game the turn merely mentioned. Picks arrive
    // newest-first, so the most recent match wins.
    const lead = text.split(/\n\s*\n/)[0]?.toLowerCase() ?? "";
    return picks.find((pick) => lead.includes(pick.game_title.toLowerCase()));
  }

  async function setVerdict(pick: Pick, verdict: Verdict) {
    setPicks((prior) =>
      prior.map((item) => (item.game_title === pick.game_title ? { ...item, verdict } : item)),
    );
    await api.setFeedback(pick.game_title, verdict).catch(() => undefined);
  }

  async function addPick(pick: Pick) {
    setPicks((prior) =>
      prior.map((item) => (item.game_title === pick.game_title ? { ...item, owned: true } : item)),
    );
    await api.addGame(pick.game_title).catch(() => undefined);
    onLibraryChanged();
  }

  // Keep the newest content in view as the answer streams in.
  useLayoutEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [messages, liveText, activeTool, streaming]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    setDraft("");
    setError(null);
    setUsage(null);
    setMessages((prior) => [...prior, { role: "user", content: trimmed }]);
    setStreaming(true);
    setLiveText("");
    setLiveNotes([]);

    let answer = "";
    const notes: string[] = [];
    const handle = (event: ChatEvent) => {
      switch (event.kind) {
        case "delta":
          // Live fragments of the round in progress; the closing event wins.
          setLiveText((prior) => prior + event.text);
          break;
        case "thinking":
          notes.push(event.text);
          setLiveNotes([...notes]);
          setLiveText(""); // the round closed as working notes, not the answer
          break;
        case "tool":
          setActiveTool(event.tool);
          setLiveText("");
          break;
        case "text":
          answer = answer ? `${answer}\n\n${event.text}` : event.text;
          setLiveText(answer);
          break;
        case "error":
          setError(event.message);
          break;
        case "done": {
          const caption = formatUsage(event.usage);
          setUsage(caption);
          onUsage(caption); // the desktop sidebar shows the running cost
          break;
        }
      }
    };

    try {
      await streamChat(trimmed, handle);
    } catch {
      setError("The connection dropped mid-answer. Try again?");
    }

    if (answer) {
      const entry: Message = { role: "assistant", content: answer };
      if (notes.length) entry.notes = notes;
      setMessages((prior) => [...prior, entry]);
      // The turn may have saved a pick or touched the library; refresh both so
      // the new reply can render as a card with working actions.
      await refreshPicks();
      onLibraryChanged();
    }
    setLiveText("");
    setLiveNotes([]);
    setActiveTool(null);
    setStreaming(false);
  }

  async function newConversation() {
    await api.resetConversation().catch(() => undefined);
    setMessages([]);
    setUsage(null);
    setError(null);
  }

  const empty = loaded && messages.length === 0 && !streaming;

  return (
    <>
      <div className="screen" ref={scrollRef}>
        {empty && (
          <div className="empty-state">
            <Logo steam className="steam" />
            <h2>WHAT ARE WE PLAYING?</h2>
            <p>Tell me the mood, the time you've got, and I'll find something worth buying.</p>
            <div className="starters">
              {STARTERS.map((starter) => (
                <button key={starter.label} onClick={() => void send(starter.text)}>
                  {starter.label}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.length > 0 && <div className="day-divider">Conversation</div>}

        {messages.map((message, index) =>
          message.role === "user" ? (
            <div className="bubble-user" key={index}>
              {message.content}
            </div>
          ) : (
            <div key={index}>
              {message.notes && message.notes.length > 0 && (
                <details className="worked">
                  <summary>How I picked this</summary>
                  <ul className="notes">
                    {message.notes.map((note, noteIndex) => (
                      <li key={noteIndex}>{note}</li>
                    ))}
                  </ul>
                </details>
              )}
              <RecCard
                text={message.content}
                pick={pickFor(message.content)}
                onFeedback={(verdict) => {
                  const pick = pickFor(message.content);
                  if (pick) void setVerdict(pick, verdict);
                }}
                onAdd={() => {
                  const pick = pickFor(message.content);
                  if (pick) void addPick(pick);
                }}
              />
            </div>
          ),
        )}

        {streaming && (
          <>
            {liveNotes.length > 0 && (
              <div className="tool-chips">
                {liveNotes.slice(-1).map((note, index) => (
                  <span className="chip" key={index}>
                    {note.length > 64 ? `${note.slice(0, 63)}…` : note}
                  </span>
                ))}
              </div>
            )}
            {activeTool && (
              <div className="tool-chips">
                <span className="chip live">
                  {TOOL_LABELS[activeTool] ?? `🔧 ${activeTool.replace(/_/g, " ")}`}
                </span>
              </div>
            )}
            {liveText ? (
              <div className="rec-card streaming">
                <div className="why">
                  <Markdown text={liveText} />
                  <span className="cursor" aria-hidden="true" />
                </div>
              </div>
            ) : (
              !activeTool && (
                <div className="thinking">
                  <Logo steam className="steam" />
                  <span>cooking something…</span>
                </div>
              )
            )}
          </>
        )}

        {error && <div className="error-note">{error}</div>}
        <div ref={endRef} />
      </div>

      {usage && !streaming && <div className="usage">{usage}</div>}

      {messages.some((message) => message.role === "assistant") && !streaming && (
        <div className="quick-replies">
          {QUICK_REPLIES.map((reply) => (
            <button key={reply.label} onClick={() => void send(reply.text)}>
              {reply.label}
            </button>
          ))}
          <button onClick={() => void newConversation()} title="Start a new conversation">
            ✨ New
          </button>
        </div>
      )}

      <div className="composer">
        <textarea
          rows={1}
          value={draft}
          placeholder="Message GameGusto…"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends on a physical keyboard; Shift+Enter is a newline.
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send(draft);
            }
          }}
        />
        <button
          className="send"
          onClick={() => void send(draft)}
          disabled={streaming || !draft.trim()}
          aria-label="Send"
        >
          ▶
        </button>
      </div>
    </>
  );
}
