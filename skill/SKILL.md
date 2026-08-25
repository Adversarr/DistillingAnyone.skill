---
name: distilling-anyone
description: Distill a named how-to ability from pointed-at histories and verify it with sealed testers.
disable-model-invocation: true
---

# DistillingAnyone

The **main agent** turns pointed-at histories into one how-to **SomeSkill**, then runs sealed **blind** testers against a frozen **rubric**.

Resolve this package’s root from the loaded skill (not cwd). Prompts and references live beside this file. Scratch is parent-only (`/tmp/distill-<run-id>/` unless the user names another).

User injects: a change to the ability or a live rule **cancels** in-flight testers and does not consume the **wave** cap. Other injects queue for the next wave.

## Stages

1. **Intake** — [`prompts/intake.md`](prompts/intake.md). Path and extractor-or-Read per pointer required; else stop.
2. **Import** — [`prompts/import.md`](prompts/import.md). Teacher corpus = human turns in scratch.
3. **Analyze** — [`prompts/analyze.md`](prompts/analyze.md) + [`references/evidence.md`](references/evidence.md). Thin corpus → stop.
4. **Problems** — [`prompts/problems.md`](prompts/problems.md). Zero checkable problems → stop.
5. **Rubric** — [`prompts/rubric.md`](prompts/rubric.md). Freeze before draft. Rubric files stay in scratch.
6. **Draft** — create or evolve SomeSkill at the given path (attached packager may set layout; this skill owns the body). When-to-use, steps, insisted methods, output shape. Executable rules. Generic wording. Then **seal**.
7. **Seal** — [`references/seal.md`](references/seal.md). Tester copy only.
8. **Wave** — [`prompts/tester.md`](prompts/tester.md). Wait for every tester. Cap 8 at once unless the user raised it.
9. **Grade + patch** — [`prompts/patch.md`](prompts/patch.md). All-tasks **HIT** → stop. Else seal and wave again. 3-wave miss → fail (not ship). User stop → stop.

The frozen **rubric** is the only release gate.
