# Analyze

Read [`../references/evidence.md`](../references/evidence.md) first.

## Extract

From scratch `human/` only:

1. List **insisted** methods: constraints the human repeated or that a **did** accepted.
2. Anchor each with file + turn. Kind = said / did / others. Mark **infer** separately; it is not a method.
3. Keep contradictions. Mark **thin** rows.

## Table

Write scratch `coverage.md`:

| Dimension | Kind | Anchor | Status |
|---|---|---|---|
| … | said/did/others | file:turn | live / thin / contradiction |

The named ability is the only scope. Chat and project identifiers stay out of this table’s wording (methods, not fingerprints).

## Stop

Zero live methods, or the ability has no support → stop. Report **thin**. Do not pad.

## Done

`coverage.md` exists. Every live method has an anchor. Every asked dimension is live, thin, or a kept contradiction. No invented rows.
