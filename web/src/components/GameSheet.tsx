import { useEffect, useRef, useState } from "react";
import { api, type GameRecord, type Platform } from "../api";
import { COURSES, type Course, type TasteVerdict, VERDICTS, courseOf, verdictOf } from "../taste";

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

  // The user's rating. Persisted as it changes (below) rather than behind a save
  // button — a tap should just stick. A dirty flag makes closing the sheet
  // refresh the card badge without a reload after every single tap.
  const [taste, setTaste] = useState<TasteVerdict | null>(record.taste);
  const [course, setCourse] = useState<Course | null>(record.course);
  const [note, setNote] = useState(record.taste_note ?? "");
  const rated = useRef(false);

  // The sheet closes without reloading; if the rating changed, reload on close
  // so the library card picks up the new badge.
  function close() {
    if (rated.current) onChanged();
    else onClose();
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  async function saveTaste(
    nextTaste: TasteVerdict | null,
    nextCourse: Course | null,
    nextNote: string,
  ) {
    setTaste(nextTaste);
    setCourse(nextCourse);
    rated.current = true;
    setError(null);
    try {
      await api.setTaste(record.dedup_key, nextTaste, nextCourse, nextNote.trim() || null);
    } catch (cause) {
      const detail = cause instanceof Error && cause.message ? ` (${cause.message})` : "";
      setError(`Couldn't save your rating${detail}`);
    }
  }

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
      <button className="sheet-veil" onClick={close} aria-label="Close details" />
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

        {/* The user's own take. Each chip carries its meaning as a caption, so
            the metaphor teaches itself — no legend to hunt for. It saves on tap;
            teaching the agent is the whole point. */}
        <div className="rating">
          <div className="rating-head">
            <span>How was it?</span>
            <span className="rating-hint">
              {verdictOf(taste)?.hint ?? "your take, reviews aside"}
            </span>
          </div>
          <div className="chips">
            {VERDICTS.map((option) => (
              <button
                key={option.value}
                className="chip"
                aria-pressed={taste === option.value}
                title={option.hint}
                onClick={() =>
                  void saveTaste(taste === option.value ? null : option.value, course, note)
                }
              >
                <span className="chip-emoji">{option.emoji}</span>
                {option.label}
              </button>
            ))}
          </div>

          <div className="rating-head">
            <span>What’s it for?</span>
            <span className="rating-hint">
              {courseOf(course)?.hint ?? "when do you reach for it?"}
            </span>
          </div>
          <div className="chips">
            {COURSES.map((option) => (
              <button
                key={option.value}
                className="chip"
                aria-pressed={course === option.value}
                title={option.hint}
                onClick={() =>
                  void saveTaste(taste, course === option.value ? null : option.value, note)
                }
              >
                <span className="chip-emoji">{option.emoji}</span>
                {option.label}
              </button>
            ))}
          </div>

          <input
            className="rating-note"
            value={note}
            placeholder="A word on why (optional) — e.g. combat sings in short bursts"
            onChange={(event) => setNote(event.target.value)}
            onBlur={() => {
              if ((note.trim() || null) !== (record.taste_note ?? null))
                void saveTaste(taste, course, note);
            }}
          />
        </div>

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
          <button disabled={busy !== null} onClick={close}>
            Close
          </button>
        </div>
      </div>
    </>
  );
}
