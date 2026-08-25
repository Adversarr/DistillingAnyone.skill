# Patch

## Grade first

Parent only. For each brief, score the returns against the frozen **rubric**: HIT / MISS / WRONG.

**All-tasks HIT** = every brief’s required items HIT (use the best return if a brief had several testers).

Invented steps the teacher never **said**/**did** are **infer**. Record them. They are not patches.

## Classify each gap

| Class | Action |
|---|---|
| **supplement** | SomeSkill is missing an insisted method testers MISS. Add it. |
| **already-there** | The live rule was in SomeSkill and testers followed it. Leave it. |
| **infer** | Testers invented a step. Leave SomeSkill. |
| **conflict** | A new fact contradicts a live rule that still HITs. Note it for the user. Keep the live rule. |

Apply queued user injects here. Sanitize: methods in generic terms; workplace names, private repos, unpublished algorithm names, and raw chat stay out.

Surgical edit. One meaning, one place.

## Done

SomeSkill either (a) all-tasks HIT and you stop, (b) patched and ready to **seal** again, (c) 3-wave miss recorded as fail (not ship), or (d) a conflict note is waiting on the user. Wave count incremented only when testers ran to completion.
