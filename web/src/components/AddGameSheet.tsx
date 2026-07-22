import { useEffect, useMemo, useRef, useState } from "react";
import { api, type GameRecord, type GameSuggestion, type Platform } from "../api";

/** Platform families, so "Switch" counts as owned against "Nintendo Switch". */
function owns(owned: Platform[], name: string): boolean {
  const b = name.toLowerCase();
  return owned.some((platform) => {
    const a = platform.name.toLowerCase();
    return a === b || b.includes(a) || a.includes(b);
  });
}

/** Owned platforms first, so the one the user actually has is the default. */
function byOwnedFirst(owned: Platform[], names: string[]): string[] {
  return [...names].sort((a, b) => Number(owns(owned, b)) - Number(owns(owned, a)));
}

/**
 * Add-a-game sheet. Type a title, get live IGDB suggestions with box art, pick
 * one, then choose the platform you own it on from the platforms that game
 * actually shipped on. A title IGDB has never heard of can still be added by
 * hand. Store-search feel, but backed by the games industry's own catalogue.
 */
export function AddGameSheet({
  owned,
  platforms,
  onClose,
  onAdded,
}: {
  owned: GameRecord[];
  platforms: Platform[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<GameSuggestion[]>([]);
  const [searching, setSearching] = useState(false);
  // A synthetic suggestion with no platforms represents the manual path.
  const [selected, setSelected] = useState<GameSuggestion | null>(null);
  const [platform, setPlatform] = useState("");
  const [adding, setAdding] = useState(false);
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

  // Debounced so typing doesn't fire an IGDB call per keystroke; paused once a
  // game is chosen, so confirming a pick doesn't re-search its own name.
  useEffect(() => {
    if (selected) return;
    const term = query.trim();
    if (term.length < 3) {
      setResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timer = setTimeout(() => {
      api
        .catalogSearch(term)
        .then((data) => setResults(data.results))
        .catch(() => setResults([]))
        .finally(() => setSearching(false));
    }, 280);
    return () => clearTimeout(timer);
  }, [query, selected]);

  const platformOptions = useMemo(
    () => (selected ? byOwnedFirst(platforms, selected.platforms) : []),
    [selected, platforms],
  );

  function choose(suggestion: GameSuggestion) {
    setSelected(suggestion);
    setResults([]);
    setPlatform(byOwnedFirst(platforms, suggestion.platforms)[0] ?? "");
  }

  /** Add exactly what was typed — for a game IGDB does not list. */
  function chooseManual() {
    setSelected({ name: query.trim(), platforms: [], cover_url: null });
    setPlatform("");
  }

  function back() {
    setSelected(null);
    setPlatform("");
  }

  async function add() {
    const title = (selected?.name ?? query).trim();
    if (!title) return;
    setAdding(true);
    try {
      await api.addGame(title, platform.trim() || undefined);
      onAdded();
      onClose();
    } catch {
      setAdding(false);
    }
  }

  return (
    <>
      <button className="sheet-veil" onClick={onClose} aria-label="Close" />
      <div className="sheet" role="dialog" aria-label="Add a game">
        <div className="grab" />
        <h3>Add a game you own</h3>

        {selected ? (
          <>
            <div className="selected-game">
              {selected.cover_url ? (
                <img className="sugg-cover" src={selected.cover_url} alt="" referrerPolicy="no-referrer" />
              ) : (
                <span className="sugg-cover placeholder">🎮</span>
              )}
              <span className="sugg-name">{selected.name}</span>
              <button className="link" onClick={back} aria-label="Choose a different game">
                change
              </button>
            </div>

            <div className="sheet-row">
              <span>Platform</span>
              {platformOptions.length > 0 ? (
                <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
                  {platformOptions.map((name) => (
                    <option key={name} value={name}>
                      {name}
                      {owns(platforms, name) ? " ✓ you own" : ""}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  value={platform}
                  placeholder="optional"
                  onChange={(event) => setPlatform(event.target.value)}
                />
              )}
            </div>

            <div className="sheet-actions">
              <button disabled={adding} onClick={() => void add()}>
                {adding ? "Adding…" : "＋ Add to library"}
              </button>
              <button onClick={onClose}>Cancel</button>
            </div>
          </>
        ) : (
          <>
            <div className="meta">Type at least three letters to search IGDB.</div>
            <div className="sheet-row">
              <span>Title</span>
              <input
                ref={inputRef}
                value={query}
                placeholder="e.g. Hollow Knight"
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            {results.length > 0 && (
              <div className="suggestions">
                {results.map((suggestion) => {
                  const already = ownedTitles.has(suggestion.name.trim().toLowerCase());
                  return (
                    <button
                      key={`${suggestion.name}|${suggestion.platforms.join(",")}`}
                      onClick={() => choose(suggestion)}
                    >
                      {suggestion.cover_url ? (
                        <img
                          className="sugg-cover"
                          src={suggestion.cover_url}
                          alt=""
                          loading="lazy"
                          referrerPolicy="no-referrer"
                        />
                      ) : (
                        <span className="sugg-cover placeholder">🎮</span>
                      )}
                      <span className="sugg-text">
                        <span className="sugg-name">{suggestion.name}</span>
                        <span className="sugg-plats">
                          {suggestion.platforms.slice(0, 3).join(" · ") || "—"}
                          {already ? " · in library" : ""}
                        </span>
                      </span>
                      <span className="sugg-add">＋</span>
                    </button>
                  );
                })}
              </div>
            )}

            {query.trim().length >= 3 && !searching && (
              <button className="link add-manual" onClick={chooseManual}>
                {results.length > 0 ? `Not listed? Add “${query.trim()}” manually` : `Add “${query.trim()}” manually`}
              </button>
            )}

            <div className="sheet-actions">
              <button onClick={onClose}>Cancel</button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
