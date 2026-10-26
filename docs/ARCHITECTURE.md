# Architecture

Purpory ships an independently installable CLI and Wails desktop. Both call
shared services in `internal/app`. Source code is one Material alongside
documents, notes, media, and external references; core storage and retrieval
never require Git or a programming language.

```text
CLI / Wails UI → application services → project knowledge → SQLite
                                      └ optional embeddings → Ollama / OpenAI-compatible

update: discover Materials → extract facts → resolve relations → atomic publish
Intent ── durable link ──▶ Material / Knowledge
```

## Packages

- `cmd/purpory`: standalone CLI entry point.
- `internal/cli`: commands, preflight, and Session status hooks.
- `internal/app`: shared orchestration and delivery DTOs.
- `internal/project`: domain-neutral Project, Resource, View, and Session models;
  local folders and Git are observation adapters.
- `internal/material`, `extract`, `resolve`: discovery, format adapters, and
  structural relationship resolution.
- `internal/memory`, `graph`: durable memory and the canonical graph.
- `internal/store`: SQLite persistence and forward-only migrations.
- `internal/prepare`: request validation, hint contracts, and rendering.
- `internal/ollama`, `openai`: embedding providers; Ollama also owns local
  model discovery, installation, and startup.
- `internal/integration`: Codex and Claude instructions and lifecycle hooks.
- `frontend`: React UI backed by generated Wails bindings.

Dependencies point inward. Provider, transport, filesystem, and SQLite details
stay outside the core domain. Small interfaces live beside their consumers.
There is one shared implementation for each CLI/desktop behavior.

## Projects and observations

Projects are created only through explicit registration or the desktop's
Projects page. Ordinary commands resolve registered Projects from their working
directory and never create one as an observation side effect. Multiple matching
Projects produce an ambiguity error.

Resources describe observed workspaces; Views describe local folders/worktrees.
Git emits one Resource with all local worktrees as Views. Ordinary folders use
the same model. Unassigned hooks retain path-only observations, never prompts,
transcripts, or Sessions. Sessions attach only after assignment.

The desktop reads committed state on open/focus and writes only after explicit
actions. Every page uses one Project scope. A Project can be opened while its
local root is unavailable. Default Project/View selection uses total ordering
including stable IDs.

Session-end hooks record status only. Purpory does not read transcripts, run
background inference, or start a worker. Explicit memory writes and the invoked
`purpory-curate` session are the only durable mutation paths.

## Knowledge and durable memory

`nodes` and `edges` are the physical graph. Node kind identifies Intent,
Material, Knowledge, or Reference; subkind contains adapter details. Ownership
separates observed and durable lifecycles.

`update` selects an available View per Resource, namespaces equal relative
paths, fingerprints Materials, reuses unchanged facts, resolves claims, and
publishes one observed snapshot in a transaction. Git indexing uses Git's own
ignore rules. Extraction verifies the fingerprint of the same bytes it parses.
If a Material changes between discovery and extraction, the update aborts.
A database revision check rejects a snapshot computed before another update
committed, including updates from separate CLI/desktop processes.

Updates never delete durable edges. Missing targets stay visible as
`state=missing` and reconnect when the same stable reference returns.
Explicit graph edits may retire edges; normal retrieval excludes retired edges.

Memory stores stable topic-first dot keys, kinds, values/source references,
content hashes, and immutable version history. Manual batches validate expected
hashes and apply atomically. Explicit graph editing has a project undo log:
rollback restores the baseline only if later writes have not changed it;
checkpoint accepts the current state. Disabling edit mode only revokes access.

## Retrieval

`query` and `prepare` share direction-aware Typed Personalized PageRank.
Optional embedding top-k, exact matches, and recent ordered navigation seed the
walk. Seed normalization and result ties use stable ordering. Session history
is an explicit input; opening evidence changes subsequent retrieval.

`prepare` directly retrieves context without model classification. Greetings,
oversized retrieval queries, and no matching evidence return `skip`.
There are no generated questions or model-decision records.

Preflight returns at most three content-bearing, unopened signposts within a
token budget. CLI query returns five content-free candidates by default.
Explain loads selected evidence only; path connects candidates without loading
content. JSON preserves the same progressive boundary. Navigation events are
operational retrieval signals and never authorize durable writes.

With the same project snapshot, session state, and embedding response, retrieval
returns the same result. Embeddings retain their bounded timeout and optional
fallback behavior.

## Embeddings and persistence

Embedding is the sole global model binding, with provider, model, and dimensions.
Ollama and OpenAI-compatible APIs implement the embedding consumer's interface.
Each Project's vectors are isolated by the complete binding identity. Explicit
sync fills missing/stale vectors; durable writes refresh affected vectors.

Provider endpoints and encrypted API keys are global SQLite state. Environment
overrides stay process-local. A random sibling master key encrypts credentials
using AES-GCM. CLI secrets arrive only through standard input. External
embedding providers receive knowledge/query text.

Migrations are forward-only. Migration 27 backs up populated existing databases
before dropping obsolete operational tables/settings/credentials. Old migration
files exist only to read existing databases, with no compatibility execution
paths. Canonical memories, graph links, Sessions, navigation, reviews, and
embeddings survive the migration.

Maintenance keeps each memory's original version plus its 99 newest revisions
and review-referenced versions, the newest 100 navigation events per Session,
removes orphan embeddings, and runs SQLite VACUUM.
