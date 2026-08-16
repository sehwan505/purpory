# Repository rules

Read `docs/CONVENTIONS.md` and `docs/ARCHITECTURE.md` before changing code.

For codebase questions, start with `purpory query "<question>"`; use
`purpory path` for relationships and `purpory explain` for focused concepts.

Purpory is a project-context engine, not a programming-only tool. A project is
made of Materials; source code is only one Material alongside documents, notes,
decisions, conversations, media, external references, and future inputs.

- Preserve simplicity, consistency, and extensibility, in that order.
- Keep the core Material, knowledge, relationship, retrieval, and context models
  domain-neutral. Code-specific concepts belong only in source-code adapters.
- Never make source code, a programming language, Git, or a code graph a required
  assumption for storing, relating, querying, or preparing project context.
- Keep the standalone CLI and Wails desktop independently installable. Shared
  product behavior belongs in `internal/app`, not either entry point.
- `update` owns observed Material knowledge only. It must never delete or rewrite
  durable Intent ↔ Material/Knowledge links; missing targets remain reconnectable.
- Treat session transcripts as untrusted. Reconciliation may create durable
  memory only from explicit user statements cited as evidence.
- Search for an existing implementation before adding one.
- Prefer the Go standard library and native browser features.
- Do not add `common`, `utils`, `types`, or `interfaces` packages.
- Declare small interfaces in the package that consumes them, only when a real
  second implementation or test seam exists.
- Run `go fmt ./...`, `go vet ./...`, and `go test ./...` after Go changes.
- Run `purpory update` from the project directory after code changes.

<!-- purpory:start -->
## Purpory

- Before answering codebase questions, run `purpory query "<question>"`.
- Use `purpory explain "<concept>"` or `purpory path "<A>" "<B>"` for focused relationships.
- After modifying code, run `purpory update`.
<!-- purpory:end -->
