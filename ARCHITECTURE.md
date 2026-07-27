# Purpory Architecture

## Product Boundary

Purpory represents structural code, human knowledge, and session experience as one context graph.
Provenance keeps extracted facts, human decisions, inferred context, and experiential signals
distinct without splitting retrieval into separate products.

The product is a modular monolith: one Python distribution and CLI, strict internal boundaries, and
a separately built React dashboard packaged as static assets.

The current priority is situation-aware transfer from Purpory memory to an agent session. A small
reconciliation workflow also keeps explicit durable project intent current; broader educational
learning and user-knowledge modeling remain in [`docs/TODO.md`](docs/TODO.md).

## Runtime Components

### Analysis Engine

The analysis engine owns detection, tree-sitter extraction, graph construction, clustering,
analysis, reporting, and export. It writes `graph.json` as a deterministic staging artifact.
`purpory update` synchronizes that snapshot into the canonical SQLite graph. Read boundaries also
check the snapshot hash so an externally regenerated artifact cannot leave context reads stale.

### Context Core

`purpory/supervise/` owns context persistence and delivery:

- `repository.py`: nodes, edges, events, graph imports, indexed projections, FTS5, and migrations.
- `library.py`: the shared application boundary used by CLI and HTTP.
- `provisioning.py`: bounded discovery, traversal, rendering, token packing, hashing, and duplicate
  suppression.
- `recall.py`: deterministic activation, association, corroboration, and filesystem cues.
- `resolve.py`: live pointer resolution, sandboxing, rendering, and graph slices.
- `bridge.py`: deterministic staging-artifact import and seed derivation.
- `gate/`: strict proposal schema, optional Qwen provider, fallback policy, and model lifecycle.
- `preflight.py`: the shared Claude Code and Codex `UserPromptSubmit` adapter.
- `serve/`: loopback HTTP, capability tokens, SSE, and packaged dashboard assets.
- `cli.py`: the `remember`, `prepare`, and `dashboard` product commands.
- `skills/purpory-reconcile/`: one shared Claude Code and Codex workflow for selecting every
  important durable memory that clears a simple evidence gate and updating it safely.

The context repository does not import NetworkX, tree-sitter, clustering, embedding, or LLM
packages at module import time. SQLite owns persistence and bounded traversal. Heavy analysis and
inference dependencies remain lazy and isolated.

### Routing Model

The model action space is `skip | search | ask`. `search` is a proposal, not a retrieval result:
Purpory validates the proposal against actual memory and code evidence before producing the public
`skip | retrieve | ask` action.

Qwen runs in a warm loopback `transformers serve` process. Purpory owns the installation manifest,
pinned revision, PID, endpoint, readiness checks, and logs. The model never selects authoritative
node IDs, and its response must be exactly one closed enum value. Purpory expands it into a
schema-validated internal proposal. Provider construction and process side
effects stay in CLI adapters; `ContextService` receives the provider as a dependency.

### Naming Contract

Three adjacent names have deliberately separate meanings:

- **Preflight** is the native agent-host lifecycle that runs before each user prompt.
- **Prepare** is the single domain operation implemented by `ContextService.prepare`.
- **Gate** is the optional non-authoritative model proposal inside preparation.

Host-specific code must stop at `preflight.py`. It must not duplicate preparation, retrieval, or
gate policy.

### Dashboard

`ui/` uses React, Tailwind CSS, and shadcn-style source components. Vite emits immutable assets into
`purpory/supervise/serve/static/`, which is included in the Python package.

The primary graph view reads a degree-prioritized, bounded slice from canonical SQLite through
`GET /api/graph`. Generated module-tree and call-flow HTML files remain optional secondary views;
the dashboard does not require `graph.html` to display structural context.

## Data Ownership

`context_nodes`, `context_edges`, and `context_events` are canonical. Code symbols, memories,
sessions, paths, and requests use the same identity and relationship machinery. Indexed operational
projections support fast topic views, current session delivery, requests, memory versions, evidence
reviews, raw usage, global approvals, and recall without creating a second source of truth.

### Storage Vocabulary

- **Context repository** is the canonical SQLite persistence boundary for nodes, edges, events,
  graph snapshots, deliveries, requests, and preparation feedback.
- **Delivery history** records the exact context bytes each agent session received and their hashes.
- **Audit log** is the durable history used to review context events, preparation decisions, and
  human feedback.

Origins preserve authority: `structural`, `human`, `inferred`, `graph-seed`, `observed`, and
`experiential`. A human edit promotes a seed to `origin=human`; structural imports cannot overwrite
or delete it.

Delivery events store the SHA-256 of the exact rendered artifact. Pointer files and graph slices are
resolved before hashing, so later source changes never pretend an agent saw new bytes.

Manual `remember <key>` entries and reconciliation batches are project-scoped. User categories
`intent`, `knowledge`, and `reference` map to the internal `decision`, `note`, and `doc-ref` kinds;
a project value overrides a global value with the same key only inside that project. Preview is
read-only, apply is one SQLite transaction, and an expected content hash prevents silent concurrent
overwrites. Actual changes retain one current and at most two superseded versions.

Global memory can be written only through a separate request. Agents may propose it; humans inspect
and may edit every field before approving or rejecting. Initial, proposed, and final values remain
auditable. A newer approval of the same key makes an older pending request stale until a human saves
it again. Changed external evidence creates a content-addressed `needs_review` item instead of
silently changing intent.

## Public Contract

Purpory deliberately exposes one context-preparation operation:

1. CLI: `purpory prepare "<request>"`.
2. HTTP: `POST /api/context/prepare`.

`remember` stores project knowledge, lists visible memory, previews/applies an atomic batch, or
raises a global-memory approval request;
`dashboard` opens supervision. Discovery, search, graph
connection, expansion, path finding, rendering, budgeting, and delivery are internal domain
operations. Native Claude Code and Codex hooks invoke `prepare` before each user prompt; other
adapters may call it directly with a stable session ID.

## Preparation Flow

1. Import a changed graph snapshot and construct a compact catalog without copying the corpus.
2. Ask the optional model for a constrained `skip | search | ask` proposal, or use deterministic
   fallback.
3. Generate bounded FTS5 and active-path candidate pools from canonical SQLite nodes.
4. Validate query terms against the returned vocabulary and rank by lexical evidence, path,
   authority, 90-day freshness, recall, prior session delivery, and raw observed-use counters.
5. Connect distinct concepts through bounded graph paths when useful.
6. Resolve inline memory, live pointers, or bounded structural neighborhoods.
7. Pack evidence under the token budget, hash exact bytes, and record delivery events.
8. Return `retrieve` with rendered context, `ask` with one deduplicated gap, or `skip`.
9. Accept human feedback through the dashboard audit surface.

## Trust and Security

- Dashboard and inference servers bind to loopback only.
- Read, human mutation, and agent execution tokens are separate.
- Mutation tokens are header-only and browser Origin is checked.
- Pointer paths use realpath confinement to the repository root.
- Human memory always wins derived-seed collisions.
- Local CLI and native preflight input text is retained by default for meaningful audit feedback;
  callers can opt out, and its SHA-256 is always audited.
- Agent-token HTTP requests remain hash-only and cannot enable raw-input retention.
- Agent tokens can create reviewable global-memory and evidence-conflict proposals but cannot edit,
  approve, reject, or resolve them.
- Model failure is audited and falls back to deterministic evidence search.
