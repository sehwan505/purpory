# Continuous Graph Lifecycle Implementation Plan

**Goal:** Keep Purpory's project graph useful over time by recording ordered exploration, using it only as a retrieval/reconciliation signal, and retiring disproven durable intent links without deleting their audit history.

**Architecture:** Preserve three boundaries: the canonical graph stores trusted nodes and active/retired durable edges; the append-only navigation trail stores ordered session actions; retrieval projections combine lexical, semantic, graph, shared-evidence, and recent-navigation signals without persisting inferred facts. Reconciliation may add or retire an intent relation only when exact user evidence grounds that operation.

**Tech Stack:** Go, SQLite migrations, existing provider/reconcile contracts, existing typed PageRank, Go standard library tests.

**Spec:** `docs/GRAPH_LIFECYCLE_PLAN.md`

> Execution note: this branch already contains user-owned graph work. Keep all changes uncommitted and preserve the dirty worktree.

### Task 1: Ordered navigation trail

**Files:**
- Create: `internal/store/migrations/022_graph_lifecycle.sql`
- Modify: `internal/prepare/contract.go`
- Modify: `internal/store/prepare.go`
- Test: `internal/store/store_test.go`

1. Add a failing store test proving append order, repeated visits, and session isolation.
2. Add the minimal `navigation_events` schema and read/append methods.
3. Run the focused store test.

### Task 2: Session-aware retrieval and reconciliation candidates

**Files:**
- Modify: `internal/app/prepare.go`
- Modify: `internal/app/service.go`
- Modify: `internal/app/reconcile.go`
- Test: `internal/app/context_graph_test.go`
- Test: `internal/app/embeddings_test.go`

1. Add failing tests proving recent ordered navigation affects ranking and candidate discovery, while opened-node suppression remains separate.
2. Reuse `pprSeeds` for decayed trail seeds in both Query and Prepare.
3. Record successful Query, Explain, Path, and Prepare deliveries.
4. Add recent navigation plus shared Material/Knowledge neighbors to the bounded Pass-B target set; never create a durable link from those signals alone.
5. Run focused app tests.

### Task 3: Auditable durable edge retirement

**Files:**
- Modify: `internal/graph/graph.go`
- Modify: `internal/reconcile/reconcile.go`
- Modify: `internal/app/reconcile.go`
- Modify: `internal/store/knowledge.go`
- Modify: `internal/store/memory.go`
- Modify: `internal/store/sqlite.go`
- Test: `internal/reconcile/reconcile_test.go`
- Test: `internal/store/store_test.go`

1. Add failing tests proving omission does not retire, explicit grounded retirement hides an edge, audit records the action, and re-adding reactivates it.
2. Add edge state/timestamps in the migration; load only active edges into retrieval.
3. Extend Pass B with explicit `retirements`, limited to presented existing relations and exact user evidence.
4. Apply add/retire operations atomically with the memory update and reconciliation audit.
5. Run focused reconcile/store tests.

### Task 4: Continuous-lifecycle E2E and verification

**Files:**
- Test: `internal/app/reconcile_integration_test.go`
- Modify: `docs/GRAPH_LIFECYCLE_PLAN.md`

1. Add one deterministic E2E test covering transcript evidence → intent nodes/links → ordered exploration → retirement/reactivation visibility.
2. Update the lifecycle plan with implemented boundaries and deferred measurement items.
3. Run `go fmt ./...`, `go vet ./...`, `go test ./...`, then `purpory update .`.
