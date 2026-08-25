# Import

## Run

For each pointer on the intake card:

1. If they named a bundled extractor: list this package’s `scripts/` directory, match their name to a file, run that file on the pointer. Prefer a human-turns flag if the extractor offers one (`--no-tools` is the usual switch).
2. If they said **Read**: read the file as-is.
3. Write the raw output under scratch `raw/`.

No match in `scripts/` → stop. Ask them to rename the extractor.

## Strip

From each raw file, keep **human** turns only (user / YOU). Write that to scratch `human/`. Agent text stays in `raw/` and is not the **teacher** corpus.

## Done

Every pointer has a `human/` file (or an explicit empty). Scratch holds raw + human. SomeSkill and the future tester copy contain none of these files.
