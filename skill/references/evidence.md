# Evidence

Use this file during **analyze**. Every method on the coverage table must satisfy these rules.

## Kinds

Label each claim as one of:

| Kind | Counts | Does not count |
|---|---|---|
| **said** | A human turn states the method or constraint | Paraphrase of agent chatter |
| **did** | A human demanded or accepted that outcome (a number they cited, a file they kept, a revert they ordered) | An agent path the human never marked |
| **others** | A third party in the corpus (review, ticket) | A title, folder name, or “they were senior on X” |
| **infer** | Your guess. Mark it. Never promote it to a method | Filling a thin row so the table looks complete |

## This counts / this does not

- **Counts as a method:** the same insistence appears in ≥2 human turns, or one turn plus a **did** the human accepted. Cite file + turn.
- **Does not count:** a chat title, a repo root, a role label, a single spicy line, an unread link, a homepage-shaped URL.
- **Counts as thin:** a dimension you were asked to cover has no **said**/**did** anchor. Write what is missing. Leave the row marked **thin**.
- **Does not count as closing thin:** inventing a method, a quote, a source, or a URL so the table is full. An honest thin table beats a padded one.
- **Counts as a contradiction:** two human turns disagree. Keep both. Date them if you can.
- **Does not count as resolving a contradiction:** picking the nicer side. The person did not resolve it; neither do you.
- **Counts as shippable text:** a short paraphrase of a durable rule.
- **Does not count as shippable text:** a full transcript, a long verbatim dump, a raw chat block.

Ground truth is the material the user pointed at. Firsthand human turns outrank commentary.
