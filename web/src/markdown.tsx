/**
 * Minimal markdown rendering for agent replies.
 *
 * The agent writes light markdown (bold, italics, strikethrough, headings,
 * bullets, links, and tables for lists of deals). A full parser would be a
 * large dependency for that surface, so this handles exactly what the prompt
 * produces and escapes everything else — no `dangerouslySetInnerHTML`, so a
 * reply can never inject markup.
 */
import { Fragment, type ReactNode } from "react";

const INLINE = /(\*\*[^*]+\*\*|~~[^~]+~~|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;

/** A table row: `| a | b |`. */
const TABLE_ROW = /^\s*\|(.*)\|\s*$/;
/** The rule under a table's header: `|---|:---:|`. */
const TABLE_RULE = /^\s*\|?(\s*:?-{3,}:?\s*\|)+\s*(:?-{3,}:?\s*)?\|?\s*$/;

function tableCells(row: string): string[] {
  return (TABLE_ROW.exec(row)?.[1] ?? "").split("|").map((cell) => cell.trim());
}

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(INLINE).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("~~") && part.endsWith("~~")) {
      // The regular price beside a sale price.
      return <del key={key}>{part.slice(2, -2)}</del>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code key={key}>{part.slice(1, -1)}</code>;
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
    if (link) {
      const href = link[2];
      // Only http(s) — never javascript: or data: from model output.
      if (/^https?:\/\//i.test(href)) {
        return (
          <a key={key} href={href} target="_blank" rel="noreferrer noopener">
            {link[1]}
          </a>
        );
      }
      return <Fragment key={key}>{link[1]}</Fragment>;
    }
    return <Fragment key={key}>{part}</Fragment>;
  });
}

export function Markdown({ text, inline = false }: { text: string; inline?: boolean }) {
  // `inline` renders the spans only — for places that supply their own element
  // (the alternatives line inside the recommendation card).
  if (inline) return <>{renderInline(text.trim(), "inline")}</>;

  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let bullets: string[] = [];
  let paragraph: string[] = [];

  const flushBullets = () => {
    if (!bullets.length) return;
    blocks.push(
      <ul key={`ul-${blocks.length}`}>
        {bullets.map((item, index) => (
          <li key={index}>{renderInline(item, `li-${blocks.length}-${index}`)}</li>
        ))}
      </ul>,
    );
    bullets = [];
  };
  const flushParagraph = () => {
    if (!paragraph.length) return;
    const body = paragraph.join(" ");
    blocks.push(<p key={`p-${blocks.length}`}>{renderInline(body, `p-${blocks.length}`)}</p>);
    paragraph = [];
  };

  for (let at = 0; at < lines.length; at++) {
    const line = lines[at].trimEnd();
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (TABLE_ROW.test(line) && TABLE_RULE.test(lines[at + 1] ?? "")) {
      flushBullets();
      flushParagraph();
      const header = tableCells(line);
      const rows: string[][] = [];
      at += 2; // past the header and its rule
      while (at < lines.length && TABLE_ROW.test(lines[at])) rows.push(tableCells(lines[at++]));
      at--; // the loop's own increment moves past the last row
      const key = `t-${blocks.length}`;
      blocks.push(
        <div className="md-table" key={key}>
          <table>
            <thead>
              <tr>
                {header.map((cell, index) => (
                  <th key={index}>{renderInline(cell, `${key}-h-${index}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {row.map((cell, index) => (
                    <td key={index}>{renderInline(cell, `${key}-${rowIndex}-${index}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
    } else if (!line.trim()) {
      flushBullets();
      flushParagraph();
    } else if (heading) {
      flushBullets();
      flushParagraph();
      // Two levels are enough for a reply: a section ("## Big titles") and an
      // item within it ("### 🥇 Skyrim — 134,97 kr"). Rendered alike, the two
      // ran together as one block of same-size bold lines.
      const Tag = heading[1].length <= 2 ? "h3" : "h4";
      const key = `h-${blocks.length}`;
      blocks.push(<Tag key={key}>{renderInline(heading[2], key)}</Tag>);
    } else if (bullet) {
      flushParagraph();
      bullets.push(bullet[1]);
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      // A horizontal rule the model sometimes emits; the design drops it.
      flushBullets();
      flushParagraph();
    } else {
      flushBullets();
      paragraph.push(line.trim());
    }
  }
  flushBullets();
  flushParagraph();
  return <>{blocks}</>;
}
