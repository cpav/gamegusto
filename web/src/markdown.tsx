/**
 * Minimal markdown rendering for agent replies.
 *
 * The agent writes light markdown (bold, italics, headings, bullets, links).
 * A full parser would be a large dependency for that surface, so this handles
 * exactly what the prompt produces and escapes everything else — no
 * `dangerouslySetInnerHTML`, so a reply can never inject markup.
 */
import { Fragment, type ReactNode } from "react";

const INLINE = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g;

function renderInline(text: string, keyPrefix: string): ReactNode[] {
  return text.split(INLINE).map((part, index) => {
    const key = `${keyPrefix}-${index}`;
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
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

export function Markdown({ text }: { text: string }) {
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

  for (const raw of lines) {
    const line = raw.trimEnd();
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
    if (!line.trim()) {
      flushBullets();
      flushParagraph();
    } else if (heading) {
      flushBullets();
      flushParagraph();
      blocks.push(<h3 key={`h-${blocks.length}`}>{renderInline(heading[2], `h-${blocks.length}`)}</h3>);
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
