import { useEffect, useMemo, useState } from "react";
import { api, type GameRecord, type Pick, type Platform, type Verdict } from "../api";
import { GameSheet } from "./GameSheet";
import { AddGameSheet } from "./AddGameSheet";

/** Initials for the placeholder tile when a record has no cover art yet. */
function initials(title: string): string {
  const words = title.replace(/[^\p{L}\p{N} ]/gu, "").split(/\s+/).filter(Boolean);
  if (!words.length) return "??";
  return (words.length === 1 ? words[0].slice(0, 2) : words[0][0] + words[1][0]).toUpperCase();
}

/** Stable per-title hue so a library reads as varied but never reshuffles. */
function hue(title: string): number {
  let hash = 0;
  for (const char of title) hash = (hash * 31 + char.charCodeAt(0)) % 360;
  return hash;
}

/** Platform families, so "Switch" matches "Nintendo Switch" (mirrors platform_match.py). */
function platformMatches(filter: string, value: string): boolean {
  const a = filter.toLowerCase();
  const b = value.toLowerCase();
  return a === b || b.includes(a) || a.includes(b);
}

export function LibraryView({ reloadKey }: { reloadKey: number }) {
  const [records, setRecords] = useState<GameRecord[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [picks, setPicks] = useState<Pick[]>([]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("All");
  const [list, setList] = useState(false);
  const [selected, setSelected] = useState<GameRecord | null>(null);
  const [adding, setAdding] = useState(false);
  const [memoryDown, setMemoryDown] = useState(false);

  async function reload() {
    try {
      const [library, platformData, pickData] = await Promise.all([
        api.library(),
        api.platforms(),
        api.picks(),
      ]);
      setRecords(library.records);
      setMemoryDown(!library.memory_available);
      setPlatforms(platformData.platforms);
      setPicks(pickData.picks);
    } catch {
      setMemoryDown(true);
    }
  }

  useEffect(() => {
    void reload();
  }, [reloadKey]);

  const genres = useMemo(() => {
    const found = new Set<string>();
    for (const record of records) if (record.genre) found.add(record.genre);
    return [...found].sort((a, b) => a.localeCompare(b)).slice(0, 6);
  }, [records]);

  const filters = useMemo(
    () => ["All", ...platforms.map((platform) => platform.name), ...genres],
    [platforms, genres],
  );

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return records.filter((record) => {
      if (filter !== "All") {
        const inPlatform = record.platforms.some((value) => platformMatches(filter, value));
        const inGenre = record.genre === filter;
        if (!inPlatform && !inGenre) return false;
      }
      if (!needle) return true;
      // Instant client-side search across the fields people actually recall.
      return (
        record.title.toLowerCase().includes(needle) ||
        (record.genre ?? "").toLowerCase().includes(needle) ||
        record.platforms.some((value) => value.toLowerCase().includes(needle))
      );
    });
  }, [records, query, filter]);

  async function setVerdict(pick: Pick, verdict: Exclude<Verdict, null>) {
    const next = pick.verdict === verdict ? null : verdict;
    setPicks((prior) =>
      prior.map((item) => (item.game_title === pick.game_title ? { ...item, verdict: next } : item)),
    );
    await api.setFeedback(pick.game_title, next).catch(() => undefined);
  }

  return (
    <>
      <div className="screen">
        <div className="lib-head">
          <h2>LIBRARY</h2>
          <span className="count">{records.length} games</span>
          <span className="view-toggle">
            <button aria-pressed={!list} onClick={() => setList(false)} aria-label="Grid view">
              ⊞
            </button>
            <button aria-pressed={list} onClick={() => setList(true)} aria-label="List view">
              ≡
            </button>
          </span>
        </div>

        {memoryDown && (
          <div className="offline-banner">
            Can't reach your library right now — showing what loaded.
          </div>
        )}

        <div className="search-row">
          <input
            type="search"
            value={query}
            placeholder="Title, genre, platform…"
            onChange={(event) => setQuery(event.target.value)}
          />
          <button className="add" onClick={() => setAdding(true)} aria-label="Add a game">
            ＋
          </button>
        </div>

        <div className="filter-chips">
          {filters.map((name) => (
            <button key={name} aria-pressed={filter === name} onClick={() => setFilter(name)}>
              {name}
            </button>
          ))}
        </div>

        {picks.length > 0 && (
          <>
            <div className="section-line">
              <h3>Recent picks</h3>
              <span className="hint">👍/👎 teaches the agent</span>
            </div>
            <div className="picks-row">
              {picks.map((pick) => (
                <span className="pick" key={pick.game_title}>
                  🎯 {pick.game_title}
                  {pick.verdict && (
                    <span className="verdict">
                      {pick.verdict === "loved" ? "💚 loved" : "🚫 not for me"}
                    </span>
                  )}
                  <button
                    aria-pressed={pick.verdict === "loved"}
                    onClick={() => void setVerdict(pick, "loved")}
                    aria-label={`Loved ${pick.game_title}`}
                  >
                    👍
                  </button>
                  <button
                    aria-pressed={pick.verdict === "not_for_me"}
                    onClick={() => void setVerdict(pick, "not_for_me")}
                    aria-label={`Not for me: ${pick.game_title}`}
                  >
                    👎
                  </button>
                </span>
              ))}
            </div>
          </>
        )}

        <div className="section-line">
          <h3>My games</h3>
          <span className="hint">
            {visible.length === records.length
              ? "tap a cover for details"
              : `${visible.length} of ${records.length}`}
          </span>
        </div>

        {visible.length === 0 ? (
          <p className="lib-empty">
            {records.length === 0
              ? "No games yet. Add one with ＋, or ask the agent to import your purchases."
              : "Nothing matches that search."}
          </p>
        ) : (
          <div className={list ? "grid list" : "grid"}>
            {visible.map((record) => (
              <button
                className="cover-card"
                key={record.dedup_key}
                onClick={() => setSelected(record)}
              >
                <span
                  className="cover-art"
                  style={{ ["--hue" as string]: `${hue(record.title)}deg` }}
                >
                  {record.cover_url ? (
                    <img src={record.cover_url} alt="" loading="lazy" />
                  ) : (
                    <span className="initials">{initials(record.title)}</span>
                  )}
                </span>
                <span className="cover-cap">
                  <b>{record.title}</b>
                  <span>
                    {[
                      record.platforms[0],
                      record.community_review ? `⭐ ${record.community_review.score.toFixed(1)}` : "",
                    ]
                      .filter(Boolean)
                      .join(" · ") || "no details yet"}
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {selected && (
        <GameSheet
          record={selected}
          platforms={platforms}
          onClose={() => setSelected(null)}
          onChanged={() => {
            setSelected(null);
            void reload();
          }}
        />
      )}
      {adding && (
        <AddGameSheet
          owned={records}
          onClose={() => setAdding(false)}
          onAdded={() => void reload()}
        />
      )}
    </>
  );
}
