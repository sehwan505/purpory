---
name: purpory-reconcile
description: Reconcile important durable project intent from the visible conversation into Purpory memory. Use when the user explicitly states, confirms, corrects, repeats, or changes a lasting goal, direction, constraint, non-goal, rationale, or project fact, especially when they mark something as important, a root cause, or an always/never rule, or explicitly invoke $purpory-reconcile. Exclude routine progress, transient details, inferred preferences, code structure, and unconfirmed assistant proposals.
---

# Purpory Reconcile

Keep important project memory current without preserving the conversation itself. Store every distinct statement that clears the evidence gate; never discard one merely to meet a target count.

## Evidence gate

Keep a candidate only when all three answers are yes:

1. **Grounded**: Did the user explicitly state, confirm, or correct it?
2. **Durable**: Should it remain relevant after the current task ends?
3. **Consequential**: Could retrieving it change a later decision, tradeoff, or agent behavior?

Treat user emphasis (`important`, `must`, `always`, `never`, `root cause`), repetition, correction, a changed ultimate goal, and a user-supplied rationale as strong evidence. These signals help identify candidates; they do not replace the three gates.

Do not calculate an importance score or store an importance tier. Ask one concise question when grounding, durability, or consequence is materially ambiguous. Otherwise decide from explicit evidence.

## Reconcile

1. Read only the conversation already visible to you. Do not search for or retain raw transcripts.
2. Extract all candidates that clear the evidence gate. Do not impose a fixed count.
3. Merge semantic duplicates that would guide the same future decision. Keep independent goals, constraints, rationales, and confirmed facts separate even when they appeared in one message.
4. Ignore implementation progress, task logs, temporary debugging facts, structural code facts, pleasantries, and unconfirmed assistant proposals.
5. Run `purpory remember --list --root . --json`. Compare by meaning, not only wording. Update the existing logical key when the user's intent changed instead of appending a competing memory.
6. Use the smallest suitable existing representation:
   - `decision` with an `intent.*` key for goals, directions, constraints, non-goals, and their durable rationale.
   - `note` with a `knowledge.*` key for user-confirmed durable facts.
   - `doc-ref` only when a live source pointer is more durable than copied text.
7. Write each value as one concise, self-contained sentence. Include a user-supplied reason when it distinguishes the intent or explains a future tradeoff.
8. Build JSON batches with a `changes` array. Put at most 20 changes in each atomic batch; when more candidates clear the gate, use consecutive batches without dropping candidates. For an existing project-local item, copy its listed `hash` into `expectedHash`. For a new item or a project override of a global item, use `null`.
9. Preview with `purpory remember --batch <file> --root . --json`. Do not apply if the preview loses, invents, or changes the user's meaning.
10. Apply the same file with `purpory remember --batch <file> --apply --root . --json`. If any item reports `conflict`, list again and reconcile; never overwrite blindly.

Use this batch shape:

```json
{
  "changes": [
    {
      "key": "intent.product.simplicity",
      "kind": "decision",
      "value": "Keep the product simple and internally consistent as it evolves.",
      "expectedHash": null
    }
  ]
}
```

If no candidate clears all three gates, make no write and continue the user's task.
