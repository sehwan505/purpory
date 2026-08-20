# Architecture

## Shape

Purpory ships the CLI and Wails desktop as separate executables. Both call the
same application services; the CLI does not depend on Wails or frontend assets.
Wails is a delivery boundary, not the center of the program.

```text
Wails UI ─┐
          ├─ application services ─ project knowledge ─ SQLite
CLI ──────┘                       └ model boundary ──── Ollama

update: discover Materials → extract facts → resolve relations → atomic publish

Intent ── durable link ──▶ Material/Knowledge (real evidence)
```

Packages are grouped by product capability:

```text
main.go, app.go     Wails entry point, bindings, and dependency wiring
cmd/purpory/        standalone CLI entry point
internal/cli/       CLI commands, hooks, and reconciliation worker
internal/launch/    shared root, database, and project argument parsing
internal/app/      desktop/CLI-facing operations and response DTOs
internal/project/  domain-neutral workspace model and local observers
internal/material/ domain-neutral Material discovery and change detection
internal/extract/  format-specific fact extraction
internal/resolve/  project-wide relationship resolution
internal/memory/   remembered-value validation and version model
internal/graph/    structural node, edge, explanation, and path model
internal/store/    SQLite persistence and migrations
internal/ollama/   Ollama HTTP adapter
internal/prepare/  prepare request contract, ranking, budgeting, and rendering rules
internal/integration/ Codex and Claude instructions and lifecycle hooks
frontend/          React/TypeScript Wails UI
```

Directories are added only when their first behavior is implemented.

## Dependency rules

- Dependencies point inward toward product behavior.
- `material`, `extract`, `resolve`, `memory`, and `project` never import Wails,
  frontend code, SQLite drivers, or Ollama types.
- `app` coordinates capabilities but does not contain persistence or parsing.
- Wails bindings expose explicit methods and DTOs; domain structs are not UI APIs.
- Interfaces live beside the code that calls them. Go's implicit satisfaction is
  the extension mechanism; implementations never register themselves globally.
- One operation has one implementation path shared by desktop and CLI.
- The desktop and CLI are independently installable; neither launches or embeds
  the other.

## Runtime and data

- Projects are created only by an explicit user action in the desktop's global
  Projects screen or by `project add`. Ordinary CLI commands and hooks resolve
  the working directory against those registered Projects and never create one
  as an observation side effect. A desktop-created Project may remain empty
  until the user assigns an observed Resource.
- Project, Material, remembered knowledge, and session are the stable core
  concepts. Resource and view describe where a project is currently observed.
- Workspace discovery is consumed through one small observer boundary. The Git
  observer emits every local worktree as a View; ordinary folders emit the same
  model, and future domain providers can do so without changing persistence,
  session handling, or the dashboard.
- Resource and View observations may update automatically inside a registered
  Project; they can never establish a new Project.
- Agent hooks retain path-only Resource and View observations even when the
  working directory is not assigned. The desktop may explicitly add an observed
  Resource to one or more Projects; this never stores a Session, prompt, or
  transcript before assignment.
- Agent prompt/end hooks attach a Session to its observed View. Sessions without
  reliable historical View metadata remain preserved as unmapped sessions.
- Agent instructions and hooks are installed once in user-global Codex or Claude
  configuration; each invocation resolves its Project from the working directory
  reported by the hook payload.
- Session-end copies the transcript into a private queue and returns immediately.
  A detached worker treats the transcript as untrusted, accepts only memory
  grounded in explicit user statements, applies at most 20 memories per atomic
  batch with optimistic concurrency, and records an audit event. Failed jobs
  remain available for retry.
- A Material may be a document, source file, note, media item, conversation,
  external reference, or a future input. Core retrieval never requires code,
  Git, a programming language, or a code graph.
- `update` discovers one available View per assigned Resource, namespaces equal
  relative paths by Resource, reuses facts from unchanged Materials, resolves
  relationships across the combined Project snapshot, and commits Materials,
  facts, claims, and relations in one SQLite transaction.
- `nodes` and `edges` are the one physical project graph. `kind` identifies
  Intent, Material, Knowledge, and Reference; `subkind` carries adapter details.
  `owner` separates durable and observed lifecycles, while `state` keeps missing
  durable targets visible and reconnectable.
- `update` replaces only observed nodes and edges. Durable semantic edges remain
  in the same graph; an absent endpoint becomes `missing` instead of being
  deleted, then returns to `active` when observation finds the same stable ref.
- Markdown and readable text contribute searchable content with Material URI and
  locators. Binary Materials are cataloged without persisting their contents.
- The desktop is viewer-first: it reads committed state when opened or focused
  and performs writes only after an explicit user action. It never watches the
  project or starts `update` in the background. Project creation and Resource
  assignment live under Global Projects; model selection lives under Global
  Settings and applies to every Project.
- The desktop lists registered Projects and switches the entire application
  scope between their independent Workspaces. Memories, Materials, Sessions,
  reconcile runs, queries, and graphs are never combined across that selection.
- Project selection reads the last committed Workspace even when its local root
  is temporarily unavailable. Only an explicit update refreshes observations.
- Reconcile progress remains project-scoped operational state in the private
  queue. The Workspace may show its current phase on the originating Session,
  while `reconciliation_events` continues to record only committed durable
  changes and the canonical graph remains free of Workspace topology.
- Model assistance is optional. Structural indexing and stored-memory queries
  continue to work when Ollama is absent.
- The embedding model selection is global. Each Project retains independent
  vectors for that model; changing the global selection makes that Project's
  nodes pending until its next explicit embedding sync, and later durable writes
  and reconciliation refresh vectors for the selected model immediately.
- `prepare` owns the complete context gateway: bounded input validation,
  optional gate classification, embedding-first retrieval, BM25 fallback,
  graph-aware signposting, token budgeting, and decision audit. CLI, Wails, and
  agent hooks call this same path.
- Durable memory keys are topic-first dot paths. Kind remains independent, and
  old redundant kind prefixes are removed only in the query-time projection.
  This derived hierarchy adds no Topic rows or edges to the canonical graph.
- Prepare returns at most three content-free start paths: a semantic anchor, a
  distinct BM25 anchor when one exists, and an alternate branch when one exists.
  Typed edges are included only between selected signposts. `query` browses path
  branches, `explain` opens one or more nodes and records the nodes actually
  explored, and `path` exposes both topic hierarchy and physical edges. Each
  HintMap remains audited with its prepare decision.
- Embeddings rank relative top-k candidates without an absolute similarity
  cutoff. Prepare starts from one semantic candidate, then uses BM25 to fill
  distinct evidence. It suggests only content-bearing nodes; workspace Resources
  and empty graph nodes are traversal structure, not direct evidence. Repeated
  calls skip nodes actually opened in that Session without mutating the canonical
  graph.

## Product direction

Purpory retrieves Intent first and uses connected Materials or Knowledge as concrete
evidence that the intent exists in the project. Finding related source code is
one possible evidence lookup, not the primary product objective. `update` keeps
the evidence current without taking ownership of intent or its durable edges.
Workspace, View, and Session remain operational topology. Reconciliation may use
them as input and audit provenance, but never projects them as canonical graph
nodes or edges.

## Extension examples

A second model backend implements the narrow interface used by the operation that
needs completion or embeddings. A second persistence backend is not planned; it
earns an interface only when a supported use case exists. A new Material format
adds extraction behavior without changing discovery, storage, retrieval, or UI
packages. Source-code formats may share language-aware resolution internally.
