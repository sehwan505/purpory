import type {
  ContextRequest,
  ContextAction,
  ContextDecision,
  ContextPreparation,
  GlobalMemoryRequest,
  GraphPayload,
  MemoryReportDay,
  MemoryVersion,
  ModelStatus,
  NeedsReview,
  ProjectNamespace,
  Recall,
  ViewResponse,
} from "@/lib/types"

const READ_TOKEN_KEY = "purpory.read-token"
const WRITE_TOKEN_KEY = "purpory.write-token"
const AGENT_TOKEN_KEY = "purpory.agent-token"

function initializeTokens() {
  const current = new URL(window.location.href)
  const readToken = current.searchParams.get("t")
  const writeToken = new URLSearchParams(current.hash.slice(1)).get("write")
  const agentToken = new URLSearchParams(current.hash.slice(1)).get("agent")
  if (readToken) sessionStorage.setItem(READ_TOKEN_KEY, readToken)
  if (writeToken) sessionStorage.setItem(WRITE_TOKEN_KEY, writeToken)
  if (agentToken) sessionStorage.setItem(AGENT_TOKEN_KEY, agentToken)
  if (readToken || writeToken || agentToken) window.history.replaceState({}, "", current.pathname)
}

initializeTokens()

function readToken() {
  return sessionStorage.getItem(READ_TOKEN_KEY) ?? ""
}

function writeToken() {
  return sessionStorage.getItem(WRITE_TOKEN_KEY) ?? ""
}

function agentToken() {
  return sessionStorage.getItem(AGENT_TOKEN_KEY) ?? ""
}

function readUrl(path: string) {
  const url = new URL(path, window.location.origin)
  url.searchParams.set("t", readToken())
  return `${url.pathname}${url.search}`
}

async function parse<T>(response: Response): Promise<T> {
  const body = (await response.json()) as T | { error?: string }
  if (!response.ok) {
    const message = "error" in (body as object) ? (body as { error?: string }).error : undefined
    throw new Error(message || `Request failed with ${response.status}`)
  }
  return body as T
}

export async function getView() {
  return parse<ViewResponse>(await fetch(readUrl("/api/view"), { cache: "no-store" }))
}

export async function getProjects() {
  return parse<ProjectNamespace[]>(await fetch(readUrl("/api/projects"), { cache: "no-store" }))
}

export async function getRecall() {
  return parse<Recall>(await fetch(readUrl("/api/recall"), { cache: "no-store" }))
}

export async function getRequests(status?: "open" | "resolved") {
  const path = status ? `/api/requests?status=${status}` : "/api/requests"
  return parse<ContextRequest[]>(await fetch(readUrl(path), { cache: "no-store" }))
}

export async function getMemoryVersions(key: string) {
  return parse<MemoryVersion[]>(
    await fetch(readUrl(`/api/topics/${encodeURIComponent(key)}/versions`), { cache: "no-store" }),
  )
}

export async function getNeedsReviews(status?: "open" | "resolved") {
  const path = status ? `/api/memory/reviews?status=${status}` : "/api/memory/reviews"
  return parse<NeedsReview[]>(await fetch(readUrl(path), { cache: "no-store" }))
}

export async function getMemoryReport() {
  return parse<MemoryReportDay[]>(await fetch(readUrl("/api/memory/report"), { cache: "no-store" }))
}

export async function getGlobalMemoryRequests(status?: "pending" | "approved" | "rejected") {
  const path = status ? `/api/global-memory/requests?status=${status}` : "/api/global-memory/requests"
  return parse<GlobalMemoryRequest[]>(await fetch(readUrl(path), { cache: "no-store" }))
}

export async function getGraph(scope?: string, limit = 200) {
  const params = new URLSearchParams({ limit: String(limit) })
  if (scope) params.set("scope", scope)
  return parse<GraphPayload>(await fetch(readUrl(`/api/graph?${params}`), { cache: "no-store" }))
}

export async function getContextDecisions(limit = 100) {
  return parse<ContextDecision[]>(await fetch(readUrl(`/api/context/decisions?limit=${limit}`), { cache: "no-store" }))
}

export async function getModelStatus() {
  return parse<ModelStatus>(await fetch(readUrl("/api/model/status"), { cache: "no-store" }))
}

async function mutate<T>(path: string, method: "POST" | "DELETE", body?: unknown) {
  return parse<T>(
    await fetch(path, {
      method,
      headers: {
        "Content-Type": "application/json",
        "X-Purpory-Token": writeToken(),
      },
      body: method === "POST" ? JSON.stringify(body ?? {}) : undefined,
    }),
  )
}

export function createTopic(input: {
  key: string
  value?: string
  source?: string
  kind: string
}) {
  return mutate<{ key: string; action: string }>("/api/topics", "POST", input)
}

export function createProject(input: { name: string; description?: string }) {
  return mutate<ProjectNamespace>("/api/projects", "POST", input)
}

export function attachGitResource(projectId: string, input: { path: string; alias?: string }) {
  return mutate<ProjectNamespace>(
    `/api/projects/${encodeURIComponent(projectId)}/resources/git`,
    "POST",
    input,
  )
}

export function confirmTopic(key: string) {
  return mutate<{ ok: boolean }>(`/api/topics/${encodeURIComponent(key)}/confirm`, "POST")
}

export function deleteTopic(key: string) {
  return mutate<{ ok: boolean }>(`/api/topics/${encodeURIComponent(key)}`, "DELETE")
}

export function resolveRequest(id: number, key: string) {
  return mutate<{ ok: boolean }>(`/api/requests/${id}/resolve`, "POST", { key })
}

export function resolveNeedsReview(
  id: number,
  input: {
    outcome: "keep" | "change"
    change?: { key: string; kind: string; value?: string; source?: string }
  },
) {
  return mutate<NeedsReview>(`/api/memory/reviews/${id}/resolve`, "POST", input)
}

export function editGlobalMemoryRequest(
  id: number,
  input: { key: string; kind: string; value?: string; source?: string; rationale: string },
) {
  return mutate<GlobalMemoryRequest>(`/api/global-memory/requests/${id}/edit`, "POST", input)
}

export function decideGlobalMemoryRequest(id: number, decision: "approve" | "reject") {
  return mutate<GlobalMemoryRequest>(
    `/api/global-memory/requests/${id}/${decision}`,
    "POST",
  )
}

export async function prepareContext(input: {
  message: string
  sessionId?: string
  project?: string
  workingDirectory?: string
  activePaths?: string[]
  tokenBudget?: number
  retainInput?: boolean
}) {
  return parse<ContextPreparation>(
    await fetch("/api/context/prepare", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Purpory-Agent-Token": agentToken(),
        "X-Purpory-Token": writeToken(),
      },
      body: JSON.stringify(input),
    }),
  )
}

export function submitContextFeedback(
  id: number,
  input: {
    verdict: "correct" | "incorrect"
    expectedAction?: ContextAction
    expectedKeys?: string[]
    note?: string
  },
) {
  return mutate<{ decisionId: number; verdict: string }>(`/api/context/decisions/${id}/feedback`, "POST", input)
}

export function subscribeToEvents(onEvent: () => void) {
  const source = new EventSource(readUrl("/api/stream"))
  for (const event of ["topic", "seed", "request", "context", "memory", "global-memory", "project"]) {
    source.addEventListener(event, onEvent)
  }
  return () => source.close()
}
