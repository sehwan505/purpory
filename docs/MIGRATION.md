# Migration scope

Reference implementation: `../purpory-python` at upstream `main`.

## Keep

- standalone CLI workflows: `remember`, memory review and batch reconciliation,
  context request/decision audit, `prepare`, `query`, `embed`, `explain`, `path`,
  `update`, and model lifecycle management; plus the separate desktop dashboard
- working-directory project selection and the Project → Resource → View → Session
  model, with Projects created only through explicit registration
- SQLite persistence, audit history, domain-neutral Material ingestion,
  structural knowledge extraction, Codex and Claude hook installation, and
  optional Ollama and OpenAI-compatible model integration
- the existing React dashboard's useful workflows, redesigned as a native-feeling
  Wails application

## Deliberately removed

- legacy MCP graph server and graph-database exporters
- `affected`, benchmark, PR, clustering-only, label-only, report, wiki, and HTML
  analysis commands
- provider plugin registries and speculative adapters beyond the implemented
  Ollama and OpenAI-compatible HTTP boundaries
- video/transcription, broad office/database/config ingestion, long-tail language
  parsers, advanced NetworkX analytics, and duplicate watcher/hook paths
- compatibility for generated `graph.json`, legacy code-node IDs, and old caches

## Completed migration order

1. Establish conventions, module, Wails shell, CI, and packaging.
2. Implement project identity and SQLite migrations.
3. Port `remember`, `query`, `explain`, and `path` as a vertical slice.
4. Make `update` discover Materials, incrementally extract facts, resolve
   relationships, and atomically publish project knowledge.
5. Add optional Ollama features and model management.
6. Port context request resolution, gate feedback, memory review,
   embedding/Typed PPR HintMaps, and explicit fail-closed submit hooks.
7. Complete the separate dashboard and CLI, hooks, installers, and cross-OS
   release checks.

The retained workflows have runnable Go tests and a cross-platform build matrix.
Features outside the keep list require a concrete user need before porting.
