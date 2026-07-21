# GameGusto v2 design system — "The Blend"

Approved 2026-07-20 after five prototype rounds (interactive reference:
the "GameGusto v2 — Approved Design" artifact). One identity built from three
ingredients, each with a fixed job:

- **Neon Arcade — the voltage.** Hot pink and cyan with real glow, and a faint
  scanline on dark. Glow is *rationed*: it marks what matters, never wallpaper.
- **Midnight Chrome — the body.** Blue-graphite ground, silver neutrals, native
  system type, modern radii. The app should feel like a daily-driver first.
- **Backglass — the soul.** Amber lamp-light, bulb bullets on section labels,
  a chrome-trimmed marquee, warm prices. The pinball warmth nobody else has.

## Color roles (the load-bearing rule)

Every accent has ONE job. Never mix the roles.

| Role | Job | Dark | Light |
|---|---|---|---|
| `thrill` (pink) | send/flipper, kickers, user bubbles | `#ff2e97` | `#d6187f` (text/fills)¹ |
| `charge` (cyan) | everything interactive: active tab, chips, links, focus | `#2de2e6` | `#0b76d8` |
| `warmth` (amber) | wordmark, lamp bullets, prices, scores | `#ffc86a` | `#a9770e` |
| `success` (green) | confirmations, "loved" | `#5ce87f` | `#2e7d43` |

¹ `#ff2e97` on cream is ~2.7:1 — illegible as text. The raspberry is the
*text-safe* light-mode value; pure glows/halos may still use `#ff2e97`.

## Grounds & neutrals

| Token | Dark | Light |
|---|---|---|
| `bg` | `#0e101c` | `#f2ecdd` |
| `bg2` (raised ground / sheets) | `#181b2e` | `#fbf6ea` |
| `surface` (cards, inputs) | `#1b1f36` | `#fdf9ef` |
| `line` (borders) | `rgba(198,207,228,.16)` | `rgba(45,52,80,.22)` |
| `ink` (text) | `#f0efe9` | `#232b4a` |
| `sub` (secondary text) | `#9aa3b8` | `#5f6480` |

Effects: glow multiplier 1.4 (dark) / 0.5 (light); scanline opacity .06 (dark
only); vignette .25 (dark only). Light mode is a **token swap, never a second
design** — same roles, same components.

## Type

- **Press Start 2P** — brand only: wordmark, kickers, section labels. Never body.
- **System stack** (`-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`)
  — all UI and reading text.
- **Share Tech Mono** — data: meta lines, chips, prices, usage counters, timestamps.

Radii: cards 14px, buttons 10px, inputs/pills 999px.

## Logo — Panel

`logo-panel.svg` is the mark — the only one. It is used everywhere without
variation: tab bar, favicon, wordmark lockup, app icon, splash, empty states,
and while the agent is thinking.

There is deliberately no second version. An earlier draft added a "steam"
dress (amber wisps off the hot ball) for thinking and empty states; it was
removed. One mark used consistently reads as an identity, whereas a mark that
changes with context reads as two logos and makes every surface a decision.

The SVG ships dark-mode colors; when inlined, bind the deck stroke to
`--gg-ink` and keep ball and buttons on their role tokens.

`tokens.css` (custom properties, `data-theme` switched) and `tokens.json`
(machine-readable, for the frontend build) are the source of truth.
