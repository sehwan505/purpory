# Purpory

Purpory is a local project-context engine with separate Go CLI and Wails desktop
applications. It discovers a project's Materials, preserves authored knowledge,
and prepares the right context for the current task. Source code is one Material,
not a product boundary. The preserved Python implementation lives at `../purpory-python` as
the migration reference.

The rewrite follows three rules, in this order:

1. Simplicity: use the standard library and direct code before abstractions.
2. Consistency: one vocabulary and one path for each operation.
3. Extensibility: add small consumer-owned interfaces only at real boundaries.

Read [the architecture](docs/ARCHITECTURE.md), [code conventions](docs/CONVENTIONS.md),
and [migration scope](docs/MIGRATION.md) before adding code.

## Install

Install both the CLI and desktop app on macOS or Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/sehwan505/purpory/main/install.sh | sh
```

Install only one front end by adding `cli` or `app`:

```sh
curl -fsSL https://raw.githubusercontent.com/sehwan505/purpory/main/install.sh | sh -s -- cli
curl -fsSL https://raw.githubusercontent.com/sehwan505/purpory/main/install.sh | sh -s -- app
```

On Windows, download and run the PowerShell installer:

```powershell
$installer = "$env:TEMP\purpory-install.ps1"
Invoke-WebRequest https://raw.githubusercontent.com/sehwan505/purpory/main/install.ps1 -OutFile $installer
powershell -ExecutionPolicy Bypass -File $installer -Component all
```

Use `cli` or `app` instead of `all` for an individual Windows installation.
Downloaded release assets are verified against the release SHA-256 checksums.
The CLI goes in the user-local binary directory and the app goes in the user's
Applications directory, so installation does not require administrator access.

To build and install from this source checkout:

```sh
make install       # CLI and app
make install-cli
make install-app
```

Windows source builds use `./install.ps1 -Component all -Local`. Remove a local
installation with `make uninstall` or `./install.ps1 -Component all -Uninstall`.
Uninstalling executables deliberately keeps project data in `~/.purpory`.

You can also choose an individual artifact from the latest GitHub release:

- `purpory-cli-*`: standalone `purpory` executable for `PATH`.
- `purpory-desktop-*`: extract and open the desktop application.

Both are independent front ends over the same `~/.purpory/purpory.db` data.

Source installation requires Go 1.25+, Node 22+, and Wails v2.13.0:

```sh
go install github.com/wailsapp/wails/v2/cmd/wails@v2.13.0
go build -o build/purpory ./cmd/purpory
wails build
```

The standalone CLI provides automation-friendly commands:

```sh
purpory project add .
purpory project list
purpory project remove PROJECT_ID
purpory update
purpory update --json
purpory remember decision.database --kind decision --value "Use SQLite"
purpory remember decision.database --confirm
purpory remember --batch changes.json          # preview
purpory remember --batch changes.json --apply  # optimistic, atomic apply
purpory query "database decision"
purpory explain decision.database
purpory path "service.go" "Service.Update()"
purpory prepare "How does project update work?"
purpory prepare "How does project update work?" --session agent-1 --path internal/app --budget 2000 --json
purpory request list open
purpory request resolve 12 decision.database
purpory decision list
purpory decision feedback 42 incorrect --expected-action retrieve --key decision.database
purpory review list open
purpory model status
purpory model start
purpory model install qwen3-embedding:0.6b embedding
purpory model select gate qwen3:4b
purpory embed                              # fill every missing intent/knowledge embedding
purpory embed 100                          # optionally bound one backfill run
purpory integration codex install
purpory integration claude install
```

Register a Project once with `project add` before using project-scoped commands.
Ordinary CLI commands and agent hooks resolve the working directory against
registered Projects and never create one implicitly. Hooks silently do nothing
outside a registered Project.
`project remove` only unregisters a Project; its stored history is preserved and
becomes available again if the same ID is registered later.

The integration commands preserve existing agent configuration while installing
prompt and session-end hooks. Session-end snapshots are reconciled in a detached
worker; only explicit user statements may become durable project memory. Failed
jobs remain queued and are retried by the next worker. Git repositories are observed as one Resource with
all local worktrees represented as Views; non-Git folders use the same workspace
model. On first launch, preserved Python workspace, Session history, delivered
context, durable memories, versions, and reconciliation audits are copied
read-only from `~/.purpory/context.db` when present.

`purpory update` discovers all local Materials, fingerprints them, extracts only
new or changed inputs, resolves project-wide relationships, and publishes the
new knowledge snapshot atomically. The desktop reads committed state when opened
or focused and only runs an update when the user explicitly requests one.

Data is stored in `~/.purpory/purpory.db`. Set `PURPORY_DATABASE` to use another
database and `PURPORY_OLLAMA_URL` to use a non-default Ollama endpoint.
`prepare` works deterministically without a model. Use `model select gate` or set
`PURPORY_GATE_MODEL` to enable `skip | search | ask` classification;
remote gate endpoints additionally require `PURPORY_ALLOW_REMOTE_GATE=true`.
Reconciliation uses `qwen3.5:9b` by default; override it with
`model select reconcile`, `PURPORY_RECONCILE_MODEL`, or its context with
`PURPORY_RECONCILE_CONTEXT_TOKENS`. The first embedding model selected or used
is fixed for that Project. `purpory embed` backfills every missing or stale
intent/knowledge node; later memory and reconciliation writes refresh their
vectors immediately. Prepare first delivers content whose embedding clears the
similarity cutoff, fills any remaining token budget with BM25 matches, then
uses their physical-graph paths if budget remains. It does not force a minimum
result count, and contentless Resources are never direct evidence.
Agent preflight does not inject that content. It returns a compact project map
with semantic anchors first, BM25 fallback anchors next, and round-robin two-hop
paths in the remaining hint budget. Stable node IDs appear once, typed edges use
short aliases, and exact `purpory explain`, `query`, and `path` commands let the
agent load only what it needs. Explicit `purpory prepare` remains available when
direct content delivery is requested.
