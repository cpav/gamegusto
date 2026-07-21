import { useEffect, useState } from "react";
import { api, type GameRecord, type Platform } from "../api";

/**
 * Game detail sheet — the bottom-sheet layer that replaces Streamlit's ⋯ menu.
 * Holds every per-row action the old UI had: set platform, enrich, remove.
 */
export function GameSheet({
  record,
  platforms,
  onClose,
  onChanged,
}: {
  record: GameRecord;
  platforms: Platform[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [platform, setPlatform] = useState(record.platforms[0] ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const options = [...new Set([...platforms.map((p) => p.name), ...record.platforms])].filter(
    Boolean,
  );

  async function run(label: string, action: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await action();
      onChanged();
    } catch (cause) {
      // Say what actually failed. "Try again" told the user nothing and told
      // us less — a request rejected by the edge and one rejected by the API
      // looked identical, which made a real report impossible to act on.
      const detail = cause instanceof Error && cause.message ? ` (${cause.message})` : "";
      setError(`Couldn't ${label.toLowerCase()}${detail}`);
      console.error(`GameSheet: ${label} failed`, cause);
    } finally {
      // Always — not only on failure. On the success path this was left set,
      // which disabled every button in the sheet including Close. It went
      // unnoticed only because onChanged happens to unmount the sheet.
      setBusy(null);
    }
  }

  const review = record.community_review;
  const meta = [
    record.genre,
    record.estimated_playtime_hours ? `~${record.estimated_playtime_hours} h` : "",
    review ? `⭐ ${review.score.toFixed(1)}/10 (${review.source_count} sources)` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      <button className="sheet-veil" onClick={onClose} aria-label="Close details" />
      <div className="sheet" role="dialog" aria-label={`${record.title} details`}>
        <div className="grab" />
        <h3>{record.title}</h3>
        <div className="meta">{meta || "No details yet — try Enrich."}</div>

        {review?.sentiment_summary && (
          <div className="sheet-row">
            <span>Consensus</span>
            <span className="value">{review.sentiment_summary}</span>
          </div>
        )}

        <div className="sheet-row">
          <span>Platform</span>
          {options.length > 0 ? (
            <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
              <option value="">—</option>
              {options.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={platform}
              placeholder="e.g. Switch"
              onChange={(event) => setPlatform(event.target.value)}
            />
          )}
        </div>

        <div className="sheet-row">
          <span>Available on</span>
          <span className="value">{record.platform_availability.join(", ") || "unknown"}</span>
        </div>

        <div className="sheet-row">
          <span>Source</span>
          <span className="value">{record.source}</span>
        </div>

        {error && <div className="error-note">{error}</div>}

        <div className="sheet-actions">
          {platform && platform !== record.platforms[0] && (
            <button
              disabled={busy !== null}
              onClick={() =>
                void run("Save platform", () => api.setPlatform(record.dedup_key, platform))
              }
            >
              {busy === "Save platform" ? "Saving…" : "💾 Save"}
            </button>
          )}
          {/* Always available. It used to render only when the record was
              unenriched, so the moment anything filled a record in — the
              artwork backfill enriches as a side effect — the button vanished
              and there was no way to look a game up again or to replace a bad
              cover. Enrichment is idempotent, so offering it always is safe. */}
          <button
            disabled={busy !== null}
            onClick={() => void run("Enrich", () => api.enrich(record.dedup_key))}
          >
            {busy === "Enrich"
              ? "Looking up…"
              : record.is_enriched
                ? "✨ Refresh details"
                : "✨ Enrich"}
          </button>
          <button
            className="danger"
            disabled={busy !== null}
            onClick={() => void run("Remove", () => api.removeGame(record.dedup_key))}
          >
            {busy === "Remove" ? "Removing…" : "🗑 Remove"}
          </button>
          <button disabled={busy !== null} onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </>
  );
}
