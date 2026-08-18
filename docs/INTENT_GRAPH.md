# Intent-evidence memory graph

## Objective

Purpory must keep a user's durable intent authoritative while continuously
connecting it to the Materials and Knowledge that show what the project
actually became. The graph is a memory index for autonomous work, not a claim
that observed output can rewrite human intent.

## Considered hypotheses

1. Link every Intent to similar Materials during `update`. This has high recall,
   but similarity alone produces false traceability links and lets observation
   mutate durable meaning. Rejected.
2. Infer links only at query time. This avoids stored mistakes, but repeats model
   cost, loses provenance, and cannot expose drift before a related query arrives.
   Rejected as the canonical representation.
3. Link after every tool call or file write. This is timely, but couples the core
   to agent-specific event formats and mistakes touched files for completed
   evidence. Rejected.
4. Require manual links. This is precise but cannot support autonomous operation.
   Retained only as an override path through the durable link store.
5. At session end, refresh observed Materials, reconcile explicit user intent,
   and let the reconciliation model select typed relationships only to
   transcript-mentioned targets from an existence-checked Material catalog.
   Persist the memory node and durable edges in one transaction. Selected.

The selected boundary has both forms of evidence available: explicit user
statements establish durable intent, while the refreshed project snapshot
establishes which targets actually exist. A model may choose from real targets
but cannot invent a target reference or link a merely changed file. `update`
remains responsible only for observation and never deletes durable edges.
Workspace, View, and Session data remain outside the canonical graph; the
reconciliation audit records the originating session and cited user evidence.

## Canonical graph

```text
Intent (authoritative durable decision)
  ├─ applies_to ──────▶ Material (scope or constraint)
  ├─ realized_by ─────▶ Material (intended outcome)
  ├─ verified_by ─────▶ Material (confirmation)
  └─ contradicted_by ─▶ Material (conflict)
                           └─ contains ─▶ Knowledge (section, fact, entity)
                                             └─ structural relation ─▶ Knowledge
```

Decision memories are physically stored as `intent` nodes. Note and reference
memories are `knowledge` and `reference` nodes. Extracted details use the broad
`knowledge` kind and retain adapter-specific `subkind` values such as `section`
or `function`. Durable relationships are physical edges over stable memory keys,
Material URIs, or Knowledge references. Missing targets retain their kind and
switch to `state=missing`, preserving reconnectability and making drift visible.

The stored graph therefore has two ownership zones:

- `update` atomically replaces rows with `owner=observed`.
- reconcile or an explicit human action owns rows with `owner=durable`.

Both zones live in the same `nodes` and `edges` tables. `provenance` records the
writer, and query-time planning remains outside this canonical graph.

Workspace topology is operational state used as reconciliation input and is not
projected into this graph. Session IDs and transcript evidence IDs remain in the
reconciliation audit as provenance rather than becoming graph nodes.

## Retrieval

Retrieval keeps semantic and lexical evidence in separate lanes. Every embedding
match above the similarity cutoff is delivered first. If those matches do not
fill the token budget, BM25 supplies content-bearing lexical matches. The
retriever then traverses two physical-graph hops from both anchor sets while
budget remains, across durable and observed edges alike. This supports reverse
lookup and Intent-to-evidence paths without requiring the calling agent to issue
follow-up path requests.

There is no forced minimum result count: no valid match means no direct
evidence. Empty graph nodes may bridge a path but are not delivered, and
workspace Resources are never treated as evidence. Exact per-session delivery
suppression and token budgeting still apply. Personalized PageRank becomes
justified only when a multi-hop evaluation shows that this bounded traversal
loses relevant evidence.

## Research basis

- [HippoRAG](https://arxiv.org/abs/2405.14831) supports graph-based associative
  retrieval and Personalized PageRank for efficient multi-hop memory.
- [LongMemEval](https://arxiv.org/abs/2410.10813) separates indexing, retrieval,
  and reading and identifies extraction, cross-session reasoning, temporal
  reasoning, updates, and abstention as distinct long-term-memory requirements.
- [A-MEM](https://arxiv.org/abs/2502.12110) supports linking new memories into an
  evolving network rather than treating memory as an independent flat record.
- [Zep](https://arxiv.org/abs/2501.13956) supports maintaining historical
  relationships while dynamically integrating conversation and business data.
- [Generative Agents](https://arxiv.org/abs/2304.03442) shows that observation,
  reflection, and planning all matter; Purpory maps these to Material update,
  session reconciliation, and prepare-time retrieval.
- [Recovering Traceability Links in Requirements Documents](https://aclanthology.org/K15-1024/)
  shows why textual similarity alone misses or misclassifies semantic
  traceability, motivating constrained model judgment instead of all-pairs
  similarity links.

## Evaluation contract

The engine must keep these checks runnable without a model or network:

1. reconciliation commits a new Intent node and its Material edges atomically;
2. unavailable model-proposed Material references are rejected;
3. unsupported relation types and merely changed Materials are not linked;
4. `update` preserves durable edges while targets disappear and reconnects them when
   targets return;
5. Graph, Explain, and Path traverse Intent and observed evidence together;
6. prepare orders valid semantic evidence before BM25 fallback and traversed
   graph evidence within the token budget;
7. an unresolved durable target is visible rather than silently discarded;
8. Workspace Sessions never project into the canonical graph.

Future retrieval changes should be evaluated against LongMemEval's five ability
classes plus project-specific intent-to-evidence recall, false-link rate, stale
link detection, and token cost.
