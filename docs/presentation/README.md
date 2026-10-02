# Presentation pages

Two standalone pages for presenting GameGusto. Open either file directly in a
browser: fonts are inlined, and nothing loads from the network except the links.

| File | What it shows | Published copy (private until shared) |
|---|---|---|
| `menu.html` | The product, written as a restaurant menu (front of house) | <https://claude.ai/code/artifact/532c41b4-f0d2-40e3-b6d2-f6ebf1a96621> |
| `architecture.html` | How it's built: request path, chat turn, AWS services, data, IAM, deploy (back of house) | <https://claude.ai/artifact/G5BotaWfk5Dq6XePtD7F8E> |

The figures on the architecture page (resource counts, routes, tests, lines of
code, library size) were measured against the code and live AWS on 2 October
2026. Update them when the system changes.

To republish a page as a claude.ai artifact, drop its first four lines (the
doctype and meta tags): the artifact host adds its own document wrapper.
