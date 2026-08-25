# DistillingAnyone

Tells your agent to imitate "What will X do when he encounters Y".

Point at histories of how someone works. DistillingAnyone turns the human turns into one named how-to skill, then checks that skill with sealed testers who never see the teacher's answers.

## Invoke

Name this skill and provide:

- the ability
- create or evolve
- each history, and which bundled extractor to run (or **Read**)
- the path where the skill should live (required)

Extractors are in `skill/scripts/` (`extract_codex.py`, `extract_cursor.py`). The run stops on all-tasks HIT, a 3-wave miss, or you.

## License

MIT. See [LICENSE](LICENSE).
