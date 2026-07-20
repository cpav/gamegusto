import { useEffect, useRef, useState } from "react";
import { api, type GameRecord } from "../api";

/**
 * Add-a-game sheet: type a title, get live suggestions (the same Tavily
 * autocomplete the Streamlit UI used), tap one to add. Store-search feel.
 */
export function AddGameSheet({
  owned,
  onClose,
  onAdded,
}: {
  owned: GameRecord[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const [query, setQuery] = useState("");
  const [platform, setPlatform] = useState("");
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [adding, setAdding] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const ownedTitles = new Set(owned.map((record) => record.title.trim().toLowerCase()));

  useEffect(() => {
    inputRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Debounced so typing doesn't spend a Tavily call per keystroke.
  useEffect(() => {
    const term = query.trim();
    if (term.length < 3) {
      setSuggestions([]);
      return;
    }
    const timer = setTimeout(() => {
      api
        .autocomplete(term)
        .then((data) => setSuggestions(data.suggestions))
        .catch(() => setSuggestions([]));
    }, 280);
    return () => clearTimeout(timer);
  }, [query]);

  async function add(title: string) {
    setAdding(title);
    try {
      await api.addGame(title, platform.trim() || undefined);
      onAdded();
      onClose();
    } catch {
      setAdding(null);
    }
  }

  return (
    <>
      <button className="sheet-veil" onClick={onClose} aria-label="Close" />
      <div className="sheet" role="dialog" aria-label="Add a game">
        <div className="grab" />
        <h3>Add a game you own</h3>
        <div className="meta">Type at least three letters for suggestions.</div>

        <div className="sheet-row">
          <span>Title</span>
          <input
            ref={inputRef}
            value={query}
            placeholder="e.g. Hollow Knight"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && query.trim()) void add(query.trim());
            }}
          />
        </div>
        <div className="sheet-row">
          <span>Platform</span>
          <input
            value={platform}
            placeholder="optional"
            onChange={(event) => setPlatform(event.target.value)}
          />
        </div>

        {suggestions.length > 0 && (
          <div className="suggestions">
            {suggestions.map((suggestion) => {
              const already = ownedTitles.has(suggestion.trim().toLowerCase());
              return (
                <button
                  key={suggestion}
                  disabled={already || adding !== null}
                  onClick={() => void add(suggestion)}
                >
                  <span>🎮 {suggestion}</span>
                  <span className="owned">{already ? "✓ owned" : adding === suggestion ? "…" : "＋"}</span>
                </button>
              );
            })}
          </div>
        )}

        <div className="sheet-actions">
          <button
            disabled={!query.trim() || adding !== null}
            onClick={() => void add(query.trim())}
          >
            {adding ? "Adding…" : "＋ Add"}
          </button>
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </>
  );
}
