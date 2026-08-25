# Tester

Launch after [`../references/seal.md`](../references/seal.md) is done.

## Prompt

Each tester is an isolated subagent. Give them only:

- `cwd` = the sealed tester directory
- SomeSkill
- the one **brief** they own
- work artifacts already inside that copy

Ask them to do the brief, following SomeSkill. They return:

1. the work product the brief asked for
2. a short process note (what they opened, what they ran)

They do not grade. They do not see scratch, rubrics, extracts, or parent analysis.

## Count

≥1 tester per brief. No default. Cap **8** testers at once unless the user raised the cap. Remainder of this **wave** launches after that batch returns.

Wait for every tester in the wave before grading.

## Done

Every brief has ≥1 returned work product + process note, stored in scratch `returns/`. Tester `cwd` trees still contain no scratch files.
