# Seal

Use this file when building the tester workspace. Isolation is a **copy**, not a warning.

## Layout

Parent-only **scratch** (default `/tmp/distill-<run-id>/`):

- raw extracts
- human-only corpus
- coverage table
- frozen **rubric**
- parent analysis

Tester **copy** (a second directory the testers `cwd` into):

- stripped work artifacts
- SomeSkill (the current draft)
- each **brief**

## Copy steps

1. Create an empty tester directory. Done when it contains nothing from scratch.
2. Copy only the work artifacts the **brief** names (repo snapshot, reports, data files the colleague used to do the task).
3. Strip from that copy: teacher write-ups, session notes, `*transcript*`, extracts, rubric files, parent analysis, anything whose name or first lines are a solution.
4. Copy SomeSkill into the tester directory (or point testers at its path if SomeSkill already lives outside scratch).
5. Copy briefs into the tester directory. Each brief names artifacts **inside this copy**.
6. Confirm testers start with `cwd` = the tester directory and can `Read` only paths under it plus SomeSkill.

## Done

A path from scratch is unreachable from the tester `cwd` by a normal `Read`/`Glob` of that tree. The fixture below fails if a teacher file is still present.

## Fixture

[`../fixtures/seal-demo/`](../fixtures/seal-demo/) — keep `work/problem.txt`; strip `teacher/writeup.md`. After a dry seal of this tree, `teacher/` is absent and `work/problem.txt` is present.
