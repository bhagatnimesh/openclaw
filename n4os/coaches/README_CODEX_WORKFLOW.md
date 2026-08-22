# How to Use These Files with Codex

## Files

- `COACH_VISION.md` — durable long-term product and architecture context.
- `SCHOOL_COACH_V1.md` — detailed spec for the first coach.
- `CODEX_TASK_PHASE1.md` — the immediate implementation prompt.

## Recommended workflow

1. Add `COACH_VISION.md` and `SCHOOL_COACH_V1.md` to the N4OS repository, ideally under something like:
   `docs/coaches/`

2. Add `CODEX_TASK_PHASE1.md` temporarily or paste its contents into Codex.

3. In Codex, start with:

   `Read docs/coaches/COACH_VISION.md and docs/coaches/SCHOOL_COACH_V1.md, then follow the task below exactly.`

4. Paste the contents of `CODEX_TASK_PHASE1.md`.

5. Let Codex inspect the existing N4OS repo before it writes code.

6. Review Codex's proposed design. The most important question is:
   `Is this the simplest implementation that preserves Observation → Belief → Strategy → Learning?`

7. Let it implement Phase 1 only.

8. Run the tests and interact with the coach manually.

9. Capture real usage problems as regression scenarios.

10. Only then give Codex a Phase 2 task.

## Recommended Phase 2 prompt shape

Keep the durable docs unchanged. Give Codex only a new short task such as:

`Read COACH_VISION.md and SCHOOL_COACH_V1.md. Phase 1 is implemented. Now implement Phase 2 only: CandidateIntervention, DeliveredIntervention, Feedback, Outcome, and Telegram delivery. Preserve all existing Phase 1 tests. Do not add weekly reflection or proactive context triggers yet.`

This keeps context stable while making each coding iteration narrow.
