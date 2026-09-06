---
name: purpory-explore
description: Safely modify Purpory's canonical project Knowledge and graph relations when the user explicitly invokes this skill with a concrete target, or asks it to rollback or checkpoint pending exploration changes. Do not infer the target or use for ordinary code edits.
# purpory:claude-invocation-policy
---

# Purpory Explore

Treat the user's text after the skill invocation as the complete scope. This
skill controls how graph changes are made; it never decides what should change.

## Start

1. Run `purpory explore status`.
2. If `pendingChanges` is non-zero from an earlier invocation, do not mix a new
   task into that rollback baseline. Follow an explicit `rollback` or
   `checkpoint` request; otherwise ask the user to choose one.
3. Run `purpory explore on` before any write.

Use the current agent session detected by Purpory. If Purpory reports that no
agent session is available, place `--session ID` before the operation.

## Modify

- Explore progressively with `purpory query`, `purpory explain`, and
  `purpory path`. Load only the evidence needed for the user's target.
- Write canonical Knowledge with `purpory explore set KEY VALUE [REASON]` or
  `purpory explore delete KEY`.
- Write canonical relations with
  `purpory explore link SOURCE RELATION TARGET [REASON]` or
  `purpory explore unlink SOURCE RELATION TARGET`.
- Keep each reason short and tied to inspected project evidence.
- Do not use `remember`, reconciliation commands, direct database writes, or
  source-file edits as substitutes for these graph operations.
- Stay inside the target supplied with the invocation. Do not invent additional
  cleanup or graph work.

Every write is immediately visible to normal Purpory retrieval and is recorded
in the project undo log.

## Finish

1. Verify the result with `query`, `explain`, or `path`, then inspect
   `purpory explore history`.
2. On successful graph changes, run `purpory explore off` without checkpointing.
   Report that the changes remain rollbackable once.
3. On a failed or invalid partial change, run `purpory explore rollback`, then
   `purpory explore off`.
4. For an explicit acceptance request, run `purpory explore checkpoint`, then
   `purpory explore off`. Checkpoint keeps the current graph and removes its undo
   log.
5. For an explicit rollback request, run `purpory explore rollback`, then
   `purpory explore off`. Rollback restores the baseline and removes its undo
   log.

If rollback reports a later non-agent write conflict, do not overwrite it.
Turn exploration off and report the conflicting entity.
