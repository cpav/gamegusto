import { type Pick } from "../api";
import { Markdown } from "../markdown";

/**
 * An agent reply, rendered as the approved recommendation card.
 *
 * The design calls for a lit card with a kicker, a title/score row, the
 * reasoning, a highlighted price, an add-to-library action, and an alternatives
 * line. The model writes prose, so the structure comes from two sources: the
 * saved pick (title + playtime + owned, via `save_recommendation`) supplies the
 * header and the action target, and the prose is split so the price and the
 * alternatives get their own treatment. When a turn isn't a recommendation
 * there is no pick — the same card renders as a plain reply, no header.
 *
 * A suggestion carries no thumbs: you don't rate a game you haven't played. Add
 * it to your library, and rate it there once you have (see the taste system).
 */

/** Currency amounts the agent actually quotes (DKK first — the user's region). */
const PRICE = /(\d[\d.,]*\s*(?:kr\.?|DKK|SEK|NOK|EUR|USD|GBP)|[$€£]\s?\d[\d.,]*)/i;

/** Lead-ins to peel once the amount is lifted into the price box. */
const PRICE_LEAD_IN = /^(it'?s|it is|currently|right now|the price is|priced at)\b[\s:,-]*/i;

function isAlternatives(paragraph: string): boolean {
  return /^(also\b|alternatives?\b|other options\b|worth a look\b)/i.test(paragraph.trim());
}

function stripMarkdown(text: string): string {
  return text.replace(/\*\*|__|\*/g, "").trim();
}

function PriceBox({ paragraph }: { paragraph: string }) {
  const match = PRICE.exec(paragraph);
  if (!match) return null;
  const amount = stripMarkdown(match[0]);
  const detail = stripMarkdown(
    paragraph.replace(match[0], "").replace(PRICE_LEAD_IN, "").replace(/^[\s—–-]+/, ""),
  );
  return (
    <div className="price">
      <b>{amount}</b>
      <span>{detail}</span>
    </div>
  );
}

export function RecCard({
  text,
  pick,
  onAdd,
}: {
  text: string;
  pick?: Pick;
  onAdd?: () => void;
}) {
  let paragraphs = text.split(/\n\s*\n/).filter((block) => block.trim());

  // The model opens with "**Title** — genre, ~9 h, on Switch, not in your
  // library." Since the card header already carries the title from the saved
  // pick, that lead line becomes the meta row instead of repeating underneath.
  let meta = "";
  if (pick && paragraphs.length) {
    const lead = stripMarkdown(paragraphs[0]);
    if (lead.toLowerCase().startsWith(pick.game_title.toLowerCase())) {
      meta = lead.slice(pick.game_title.length).replace(/^[\s—–:,-]+/, "");
      paragraphs = paragraphs.slice(1);
    }
  }
  if (!meta && pick) {
    meta = [
      pick.estimated_playtime ? `~${pick.estimated_playtime} h` : "",
      pick.owned ? "in your library" : "not in your library",
    ]
      .filter(Boolean)
      .join(" · ");
  }

  const priceIndex = paragraphs.findIndex((block) => PRICE.test(block));
  const altsIndex = paragraphs.findIndex(isAlternatives);

  return (
    <article className="rec-card">
      {pick && (
        <>
          {/* No glyph in the string: Press Start 2P has no ▸ and would fall
              back to a mismatched face. The marker is a CSS triangle. */}
          <div className="kicker">Tonight's pick</div>
          <div className="title-row">
            <h3>{pick.game_title}</h3>
          </div>
          {meta && <div className="meta">{meta}</div>}
        </>
      )}

      <div className="why">
        {paragraphs.map((paragraph, index) => {
          if (index === priceIndex) return <PriceBox key={index} paragraph={paragraph} />;
          if (index === altsIndex) {
            return (
              <p className="alts" key={index}>
                <Markdown text={paragraph} inline />
              </p>
            );
          }
          return <Markdown key={index} text={paragraph} />;
        })}
      </div>

      {pick && (
        <div className="rec-actions">
          <button
            onClick={onAdd}
            disabled={pick.owned}
            aria-label={pick.owned ? "Already in your library" : "Add to library"}
          >
            {pick.owned ? "✓ In your library" : "➕ Add to library"}
          </button>
        </div>
      )}
    </article>
  );
}
