# Code conventions

These rules are intentionally short. `gofmt`, `go vet`, and the compiler own
formatting and basic correctness; reviewers should focus on behavior and names.

## Design rules

- Simplicity, consistency, and extensibility are evaluated in that order.
- Start with a concrete type. Add an interface at the consuming package only
  when a second implementation or a useful test seam exists.
- Keep interfaces focused, normally one to three methods. Accept interfaces and
  return concrete types.
- Keep domain behavior independent of Wails, HTTP, SQLite, and model providers.
- Keep dependency construction in `main`; do not use service locators, global
  mutable state, factories, or dependency-injection frameworks.
- Prefer synchronous code. The owner of a goroutine must also own cancellation
  and shutdown.

## Packages and files

- Package names are short, lowercase, singular nouns: `memory`, `project`,
  `store`. Never use `common`, `util`, `helpers`, `types`, or `interfaces`.
- Organize packages by product capability, not technical layer.
- Use `internal/` unless another module must import the package.
- Name files for the behavior they contain, such as `query.go` and
  `sqlite.go`. Tests use the matching `_test.go` name.
- Avoid `init`; constructors make initialization and failure explicit.

## Identifiers

- Use Go `MixedCaps`; preserve initialisms: `ID`, `URL`, `API`, `HTTP`, `JSON`,
  `SQL`, `LLM`.
- Do not repeat the package name: `memory.Record`, not `memory.MemoryRecord`.
- Getters omit `Get`: `Project.ID()`. Setters use `Set` only when mutation is
  actually part of the type's contract.
- Receivers are one or two consistent letters derived from the type, never
  `this`, `self`, or `me`.
- Prefer short local names only when their meaning is obvious in a small scope.
  Exported names describe product language, not implementation mechanics.
- One-method interfaces use the established `-er` form when natural, such as
  `Reader` or `Searcher`.

## Functions and data

- Put `context.Context` first and never store it in domain or service structs.
  Delivery boundaries may retain a framework-owned lifecycle context when the
  framework cannot pass it to bound methods.
- Validate data at process, filesystem, database, and network boundaries.
- Prefer useful zero values. Use constructors only to enforce invariants or
  acquire resources.
- Avoid `any`, reflection, and maps as object models. They are allowed only at
  truly dynamic serialization boundaries and must be converted immediately.
- Return early on errors; keep the successful path left-aligned.
- Do not use named results unless they make multiple same-typed results clearer.

## Errors and logging

- Error text is lowercase, has no trailing punctuation, and names the failed
  operation: `fmt.Errorf("load project: %w", err)`.
- Wrap errors with `%w`; inspect them with `errors.Is` and `errors.As`.
- Use sentinel or typed errors only when callers need different behavior.
- Log once at the application boundary. Libraries return errors instead of
  logging and returning the same error.
- Never log secrets, full prompts, source contents, or personal paths by default.

## Persistence and external services

- SQLite is the source of truth. Every multi-write operation uses a transaction.
- Migrations are ordered, embedded, forward-only, and tested from an empty
  database. Back up before destructive schema changes.
- Ollama is the first local model adapter. Provider-specific request and response
  types stay inside its package.
- Network clients set timeouts, honor context cancellation, bound response sizes,
  and return actionable errors.

## Frontend

- TypeScript is strict. Do not use `any` or duplicate Go-generated binding types.
- React components use `PascalCase`; hooks start with `use`; other identifiers use
  `camelCase`.
- Keep server state in the Go service. Keep transient view state in the component
  that owns it; add a state library only after native React state proves inadequate.
- Use semantic HTML, keyboard operation, visible focus, and labelled controls.

## Verification

- A behavior change includes the smallest test that would catch its regression.
- Prefer table-driven tests only when several cases share meaningful setup.
- Tests must not require the network, the user's home directory, or a running
  Ollama instance unless explicitly marked as integration tests.
- Required before merge: `go fmt ./...`, `go vet ./...`, `go test ./...`, frontend
  type-check/build, and a Wails production build on each target OS.
