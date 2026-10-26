# Intent-evidence memory graph

전체 제품 경계와 지속 관리 계약은 [Architecture](ARCHITECTURE.md)에 정리되어 있다.

## Objective

Purpory must keep a user's durable intent authoritative while continuously
connecting it to the Materials and Knowledge that show what the project
actually became. The graph is a memory index for autonomous work, not a claim
that observed output can rewrite human intent.

## Canonical graph

```text
Intent (authoritative durable decision)
  ├─ refines / depends_on / conflicts_with ─▶ Intent
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
- explicit memory and graph edits own rows with `owner=durable`.

Both zones live in the same `nodes` and `edges` tables. `provenance` records the
writer. Retired durable edges remain auditable but are excluded from the active
projection. Query-time planning remains outside this canonical graph.

Workspace topology is operational state and never becomes canonical graph
nodes. Session-end hooks record status without inspecting transcripts.

## Retrieval

Retrieval uses Typed Personalized PageRank over the physical project graph.
Embedding top-k, exact address/content matches, and the Session's most recent
ordered navigation events form one weighted personalization distribution. Repeated
visits add weight and recent events decay by position. Cosine scores are
softmaxed only relative to that top-k; they are not treated as calibrated
confidence. Durable Intent-to-evidence relations carry the strongest forward
weight, reverse containment is stronger than forward containment, and other
typed relations use a neutral default. Dot-separated durable keys remain a
query-time navigation hierarchy rather than synthetic graph edges.

Preflight renders at most three active, content-bearing, unopened nodes in Typed
PPR order. Opened nodes remain in the personalization distribution so their
neighbors inherit relevance, but they are not suggested again. The agent loads
one or more nodes with `explain`, browses a branch with `query`, or connects paths
with `path`. Typed edges are rendered only when both selected signposts share the
physical edge.

There is no forced minimum result count: no semantic, exact, or recent-session
seed means no hint. Workspace Resources remain outside the physical knowledge
graph. Exact per-session content suppression and token budgeting still apply.

Navigation events are operational state and never authorize durable graph
changes. The engine performs no model classification or automatic memory write.

## Evaluation contract

The engine must keep these checks runnable without a model or network:

1. Explicit memory batches apply atomically and reject stale expected hashes.
2. Unsupported durable relation types are rejected.
3. Updates preserve durable links while targets disappear and reconnect them
   when targets return.
4. Graph, Explain, and Path traverse Intent and observed evidence together.
5. Query and prepare use the same Typed PPR ranking, keep results within budget,
   and never include unopened node content.
6. Unresolved durable targets remain visible.
7. Workspace Sessions never project into the canonical graph.
8. Default query, explain, and path output stays within character and item
   budgets and preserves the progressive content boundary in JSON.
9. A fixed snapshot, session state, and embedding response yield identical
   retrieval results.
