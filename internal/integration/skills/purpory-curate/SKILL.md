---
name: purpory-curate
description: Curate Purpory's canonical project memory and graph relations when the user explicitly invokes this skill with a concrete target, or asks to rollback or checkpoint pending changes. Do not use for ordinary code edits or read-only lookup.
# purpory:claude-invocation-policy
---

# Purpory Curate

Treat the user's text after the skill invocation as the complete scope. The user
describes the desired outcome; this skill owns the internal Purpory commands.

## Start

1. Run `purpory explore status`.
2. If `pendingChanges` is non-zero from an earlier invocation, do not mix a new
   task into that rollback baseline. Follow an explicit `rollback` or
   `checkpoint` request; otherwise ask the user to choose one.
3. Run `purpory explore on` before any write.

Use the current agent session detected by Purpory. If none is available, place
`--session ID` before the operation.

## Curate

- Explore progressively with `purpory query`, `purpory explain`, and
  `purpory path`. Load only the evidence needed for the user's target.
- Write canonical Memory with
  `purpory explore set --kind KIND KEY VALUE [REASON]`. Preserve an existing
  Memory's kind. For a new Memory, use `decision` for Intent, `note` for
  Knowledge, or `reference` for Reference.
- Delete canonical Memory with `purpory explore delete KEY`.
- Write canonical relations with
  `purpory explore link SOURCE RELATION TARGET [REASON]` or
  `purpory explore unlink SOURCE RELATION TARGET`.
- Keep each reason short and tied to inspected project evidence.
- Do not use `remember`, reconciliation commands, direct database writes, or
  source-file edits as substitutes for these graph operations.
- Stay inside the target supplied with the invocation.

Every write is immediately visible to normal Purpory retrieval and is recorded
in the project undo log.

## Finish

1. Verify with `query`, `explain`, or `path`, then inspect
   `purpory explore history`.
2. On success, run `purpory explore off` without checkpointing and report that
   the changes remain rollbackable once.
3. On a failed partial change, run `purpory explore rollback`, then
   `purpory explore off`.
4. For explicit acceptance, run `purpory explore checkpoint`, then
   `purpory explore off`.
5. For an explicit rollback, run `purpory explore rollback`, then
   `purpory explore off`.

If rollback reports a later non-agent write conflict, do not overwrite it. Turn
exploration off and report the conflicting entity.
