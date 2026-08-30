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

Set up the current project and connect Codex in one command:

```sh
purpory setup --agent codex .
```

Use `--agent claude` for Claude Code. Setup registers and indexes the project,
then installs the selected Agent's preflight and session-end hooks. No model is
required for the first run. Codex asks you to review the installed hooks once
with `/hooks`.

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
purpory remember --kind decision --value "Use SQLite" decision.database
purpory remember --confirm decision.database
purpory remember --batch changes.json          # preview
purpory remember --batch changes.json --apply  # optimistic, atomic apply
purpory query "database decision"                       # up to five navigation hints
purpory explain game.lol.play-rule game.lol.items       # load selected evidence
purpory path "game.lol.play-rule" "file:docs/rules.md" # inspect relationships
purpory query --json "database decision"                # machine-readable navigation result
purpory prepare "How does project update work?"
purpory prepare --session agent-1 --path internal/app --budget 2000 --json "How does project update work?"
purpory request list open
purpory request resolve 12 decision.database
purpory decision list
purpory decision feedback 42 incorrect --expected-action retrieve --key decision.database
purpory review list open
purpory model status
purpory model start
purpory model install qwen3-embedding:0.6b embedding
purpory model select gate qwen3:4b
purpory model select reconcile openai gpt-4.1-mini
purpory model select embedding openai text-embedding-3-small
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

The integration commands run without a registered Project and install once in
the user's global Codex or Claude configuration. They preserve existing agent
configuration while installing prompt and session-end hooks. Session-end
snapshots are reconciled in a detached worker; only explicit user statements may
become durable project memory. Failed jobs remain queued and are retried by the
next worker. Git repositories are observed as one Resource with all local
worktrees represented as Views; non-Git folders use the same workspace model.
Codex requires reviewing the installed user hook once with `/hooks`.

`purpory update` discovers all local Materials, fingerprints them, extracts only
new or changed inputs, resolves project-wide relationships, and publishes the
new knowledge snapshot atomically. The desktop reads committed state when opened
or focused and only runs an update when the user explicitly requests one.

Data is stored in `~/.purpory/purpory.db`. Set `PURPORY_DATABASE` to use another
database and `PURPORY_OLLAMA_URL` to use a non-default Ollama endpoint.
`prepare` works deterministically without a model. Gate, reconciliation, and
embedding each select an independent `provider + model` binding. A model-only
CLI selection remains shorthand for Ollama; pass `openai` between the role and
model to use an OpenAI-compatible API. The same choice can be made with
`PURPORY_GATE_PROVIDER`, `PURPORY_RECONCILE_PROVIDER`, and
`PURPORY_EMBEDDING_PROVIDER` alongside the existing role model variables.

Configure an OpenAI-compatible API in Global Settings, or from the CLI without
putting its secret in shell history:

```sh
printf '%s\n' "$OPENAI_API_KEY" | purpory model provider configure openai \
  --url https://api.openai.com/v1 --api-key-stdin
purpory model provider status
purpory model provider clear-key openai
```

The endpoint and encrypted API key are stored in Purpory's global database; the
local encryption key is written beside the database as `purpory.db.key` with
mode `0600` where supported. Back up both files for recovery, but treat the key
file as a secret. Secrets never appear in command output. Environment
variables `PURPORY_OPENAI_API_KEY` and `PURPORY_OPENAI_BASE_URL` override saved
values; remote endpoints must use HTTPS. Selecting an external provider sends
that role's input to the configured service: gate sends the request catalog,
reconciliation sends transcript evidence, and embedding sends knowledge or
query text. Purpory never silently falls back between providers.

Reconciliation defaults to 32,768 context tokens and embedding defaults to 512
dimensions. Configure them in Global Settings or with
`PURPORY_RECONCILE_CONTEXT_TOKENS`, `PURPORY_GATE_CONTEXT_TOKENS`, and
`PURPORY_EMBEDDING_DIMENSIONS`. Provider, model, and dimensions identify stored
vectors, so changing any of them makes the affected Project nodes pending until
the next explicit `purpory embed`. Later durable writes refresh vectors for the
selected binding immediately. Query and prepare rank semantic, exact, and
recently opened seeds with direction-aware Typed PPR. Agent preflight returns at
most three content-free signposts; CLI `query` returns five by default. See the
[retrieval contract](docs/INTENT_GRAPH.md#retrieval) for weights and progression.

Run the model-free first-value and retrieval-budget checks with:

```sh
make product-eval
```
