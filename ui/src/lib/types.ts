export type Topic = {
  key: string
  value: string | null
  source: string | null
  kind: "note" | "code-area" | "doc-ref" | "decision" | "seeded"
  origin: "human" | "graph-seed"
  set_at: number
  stale: boolean
}

export type Injection = {
  key: string
  valueHash: string
  deliveredAt: number
}

export type Session = {
  id: string
  project: string | null
  items: Injection[]
}

export type ContextRequest = {
  id: number
  sessionId: string
  project: string | null
  need: string
  status: "open" | "resolved"
  resolvedKey: string | null
  createdAt: number
  resolvedAt: number | null
}

export type RecallItem = {
  key: string
  score?: number
  sessions: number
}

export type Recall = {
  preferred: RecallItem[]
  tentative: RecallItem[]
  associations: RecallItem[]
  activation: Array<{ key: string; score: number }>
}

export type ViewResponse = {
  topics: Topic[]
  sessions: Session[]
  diagnostics: {
    database: string
    integrity: string
    schemaVersion: number
    counts: Record<string, number>
  }
}

export type GraphPayload = {
  nodes: Array<{
    id: string
    label?: string
    community?: string | number
    source_file?: string
  }>
  links: Array<{
    source: string
    target: string
    confidence?: string
    relation?: string
  }>
  totalNodes: number
  totalLinks: number
  truncated: boolean
}

export type ContextAction = "skip" | "retrieve" | "ask"

export type ContextProposal = {
  action: "skip" | "search" | "ask"
  query: string | null
  scopes: Array<"human" | "code" | "session">
  keywords: string[]
  reasonCode: string
  clarification: string | null
}

export type ContextDelivery = {
  key: string
  kind: string
  origin: string
  mode: string
  stale: boolean
  truncated: boolean
  score: number
  signals: string[]
  estimatedTokens: number
  valueHash: string
  rendered?: string
}

export type ContextPreparation = {
  schemaVersion: number
  decisionId: number
  action: ContextAction
  proposal: ContextProposal
  delivery: ContextDelivery[]
  omitted: Array<{ key: string; reason: string }>
  requestId: number | null
  clarification: string | null
  model: { id: string | null; revision: string | null; latencyMs: number | null }
  fallback: string | null
}

export type ContextDecision = {
  id: number
  sessionId: string
  project: string | null
  inputHash: string
  inputText: string | null
  proposal: ContextProposal
  action: ContextAction
  delivery: ContextDelivery[]
  requestId: number | null
  model: { id: string | null; revision: string | null }
  promptVersion: string
  latencyMs: number | null
  fallback: string | null
  createdAt: number
  feedback: null | {
    verdict: "correct" | "incorrect"
    expectedAction: ContextAction | null
    expectedKeys: string[]
    note: string | null
    createdAt: number
  }
}

export type ModelStatus = {
  installed: boolean
  running: boolean
  ready: boolean
  model: string | null
  revision: string | null
  runtime: string | null
  endpoint: string | null
  pid: number | null
  startedAt: number | null
  logPath: string
  error: string | null
  providerConfigured: boolean
  providerSource: "managed" | "explicit" | "environment" | "unavailable" | "none"
  providerModel: string | null
}
