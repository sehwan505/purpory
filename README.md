# Purpory

**Give every AI agent the context you already have.**

Purpory is a local-first context graph for people supervising multiple AI agents. Structural code,
human decisions, intent, constraints, delivery history, and missing-context requests share one
versioned node, edge, and event model.

## Why Purpory

Code explains what a system does. It rarely explains why a decision was made, which constraint must
survive a refactor, or what another agent already received. Purpory makes both structural and human
knowledge available without making an LLM the source of truth.

- AST-derived code nodes, relationships, communities, and bounded graph traversal.
- Stable human-owned memory such as `decision.database.engine`.
- Exact SHA-256-pinned session delivery records.
- Deterministic recall from recency, corroboration, association, and filesystem cues.
- An optional local Qwen routing model whose proposal is verified against real evidence.
- A React, Tailwind CSS, and shadcn-style dashboard served only on loopback.

## Quick Start

```bash
uv sync --dev
npm --prefix ui install
npm --prefix ui run build

uv run purpory update .
uv run purpory remember decision.database.engine \
  --value "PostgreSQL is the transactional source of truth" \
  --category intent

PURPORY_SESSION=agent-1 uv run purpory prepare \
  "인증 흐름이 데이터베이스와 어떻게 연결되는지 설명해줘" \
  --path src/auth

uv run purpory dashboard
```

The product context surface has three commands:

```text
purpory remember <key> --value <text> | --source <pointer>
purpory remember <key> --value <text> --category intent|knowledge|reference
purpory remember <key> --value <text> --global-request --rationale <text>
purpory remember --list [--prefix <key>]
purpory remember --batch <changes.json> [--apply]
purpory prepare "<request>" [--path <active-path>] [--budget 2000] [--no-retain-input]
purpory dashboard [--port <port>]
```

`prepare` returns ready-to-inject context. If an agent needs more information, it calls `prepare`
again with the new need and the same session ID. The delivery history suppresses unchanged context
that session has already received, so the protocol does not require public catalog, search, expand,
path, pull, or push stages.

Retrieval is deterministic and explainable. It records raw observed-use counters, applies a
90-day review signal rather than expiration, and exposes Korean/English developer-memory term
expansions in search metadata instead of relying on an LLM-generated utility score.

Local CLI and Claude Code/Codex preflight requests retain their input text in the decision audit by
default so feedback can be interpreted. Use `--no-retain-input` for the CLI or set
`PURPORY_CONTEXT_RETAIN_INPUT=false` for preflight to keep only the SHA-256 hash.

Claude Code and Codex can enforce preparation before every user prompt:

```bash
purpory claude install --project
purpory codex install --project
```

These are Purpory's only host-specific integrations. Other agents can call the generic `prepare`
CLI or HTTP API without a dedicated installer.

Both installers register one native `UserPromptSubmit` preflight and install the same
`purpory-reconcile` skill. The skill keeps every explicit, durable, consequential project intent or
confirmed fact, previews changes, and applies them in conflict-checked batches. It does not use a
fixed item count or persist an opaque importance score. The hook calls the same
`ContextService.prepare` operation as the CLI and HTTP API, then either injects retrieved context,
instructs the agent to ask one clarification, or passes the prompt through. Codex requires the user
to review and trust project hooks with `/hooks`. See
[`docs/AGENT_PREFLIGHT.md`](docs/AGENT_PREFLIGHT.md).

## Agent API

Agents use one preparation route with `X-Purpory-Agent-Token`:

```http
POST /api/context/prepare
Content-Type: application/json
X-Purpory-Agent-Token: <token>

{
  "message": "전에 정한 인증 정책을 알려줘",
  "sessionId": "agent-1",
  "activePaths": ["src/auth"],
  "tokenBudget": 2000
}
```

The response contains the final `skip | retrieve | ask` action, deterministic evidence metadata,
ready rendered context, omissions, audit identity, and optional clarification. Internal catalog,
search, graph connection, expansion, path, rendering, budgeting, hashing, and deduplication remain
domain primitives rather than public protocol steps.

Agents may also raise non-authoritative `POST /api/global-memory/requests` and
`POST /api/memory/reviews` proposals. They cannot edit, approve, reject, or resolve them. Project
memory writes apply immediately; a global write is impossible until a human inspects every field,
optionally edits it, and explicitly approves it in the dashboard. Rejected proposals remain in the
audit.

## Local Model

The routing model is optional. Deterministic fallback remains usable when it is absent.

```bash
pip install 'purpory[gate]'
purpory model install
purpory model start
purpory model status
```

Purpory reuses the Hugging Face disk cache and keeps weights in one warm `transformers serve`
process. The small classifier emits exactly one of `SKIP`, `SEARCH`, or `ASK`; Purpory performs
deterministic retrieval, applies the token budget, and records the exact delivered bytes. See
[`docs/GATEWAY.md`](docs/GATEWAY.md).

## Code Graph

```bash
uv run purpory extract . --code-only
uv run purpory query "what connects authentication to billing?"
uv run purpory explain "PaymentService"
uv run purpory path "Checkout" "PaymentService"
uv run purpory update .
```

Extraction, update, clustering, and queries use the canonical SQLite graph directly. Generated
JSON, reports, and visualizations are opt-in exports:

```bash
uv run purpory export json --output graph.json
uv run purpory export report --output GRAPH_REPORT.md
uv run purpory export html
```

The dashboard renders a bounded database-backed graph without requiring generated files. Context
pointers accept `@repo/path` and `@root/path`; realpath sandboxing prevents repository escape.

For an existing checkout that has only legacy artifacts, import once and then use SQLite-backed
commands normally:

```bash
uv run purpory import purpory-out/graph.json --root .
```

Purpory does not delete the legacy directory or read it implicitly after migration. Archive or
remove it only after verifying `purpory query` and an explicit `purpory export json`.

## Architecture

Purpory is a modular monolith:

- SQLite is the canonical store for structural, human, and experiential context.
- `graph.json` is an explicit compatibility import/export artifact, never an implicit staging store.
- NetworkX is an ephemeral analysis representation, not a second source of truth.
- CLI and HTTP adapters call the same `ContextService`.
- Claude Code and Codex preflight hooks call that same service before every prompt.
- The Vite dashboard build is packaged into the Python distribution.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) and
[`docs/CONTEXT_PLANE.md`](docs/CONTEXT_PLANE.md).

## Security

- The dashboard and managed inference bind only to `127.0.0.1`.
- Read, human mutation, and agent execution tokens have separate privileges.
- Agent tokens can raise reviewable proposals but cannot curate or approve human memory.
- Routing review is exception-based; routine decisions remain auditable without requiring labels.
- Query tokens never authorize mutations; cross-origin mutations are rejected.
- Request logs contain only the method and sanitized path.
- Human-owned memory cannot be overwritten by derived graph seeds.
- Model proposals are non-authoritative and fail conservatively.

## Development

```bash
uv run pytest -q tests/test_context_gate.py tests/test_context_provisioning.py tests/test_supervise_http.py
uv run ruff check purpory/supervise tests/test_context_gate.py tests/test_supervise_http.py
uv run pyright purpory/supervise tests/test_context_gate.py tests/test_supervise_http.py
uv run bandit -q -c pyproject.toml -r purpory/supervise
npm --prefix ui run typecheck
npm --prefix ui run build
uv build
```

## Acknowledgment

Purpory's code-graph foundation began from the excellent open-source
[Graphify](https://github.com/Graphify-Labs/graphify) project. Purpory is an independent product and
repository; this acknowledgment is retained with appreciation for the original work.

## License

See [`LICENSE`](LICENSE).
