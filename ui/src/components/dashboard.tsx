import {
  AlertCircle,
  ArrowRight,
  BrainCircuit,
  Check,
  ChevronRight,
  CircleDot,
  Clock3,
  Database,
  FolderKanban,
  GitFork,
  Globe2,
  History,
  LayoutDashboard,
  LockKeyhole,
  Network,
  Plus,
  RefreshCw,
  Route,
  Save,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  Users,
} from "lucide-react"
import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input, Textarea } from "@/components/ui/input"
import {
  confirmTopic,
  attachGitResource,
  createProject,
  createTopic,
  decideGlobalMemoryRequest,
  deleteTopic,
  editGlobalMemoryRequest,
  getContextDecisions,
  getGlobalMemoryRequests,
  getGraph,
  getMemoryReport,
  getMemoryVersions,
  getModelStatus,
  getNeedsReviews,
  getProjects,
  getRecall,
  getRequests,
  getView,
  prepareContext,
  resolveRequest,
  resolveNeedsReview,
  submitContextFeedback,
  subscribeToEvents,
} from "@/lib/api"
import type {
  ContextAction,
  ContextDecision,
  ContextPreparation,
  ContextRequest,
  GlobalMemoryRequest,
  GraphPayload,
  MemoryReportDay,
  MemoryVersion,
  ModelStatus,
  NeedsReview,
  ProjectNamespace,
  Recall,
  Session,
  Topic,
  ViewResponse,
} from "@/lib/types"
import { cn } from "@/lib/utils"

type Page =
  | "overview"
  | "projects"
  | "delivery"
  | "context"
  | "memory"
  | "sessions"
  | "requests"
  | "graph"

const pageMeta: Record<Page, { eyebrow: string; title: string; description: string }> = {
  overview: {
    eyebrow: "System pulse",
    title: "Overview",
    description: "The context plane at a glance—what exists, what moved, and what still needs you.",
  },
  projects: {
    eyebrow: "Work context",
    title: "Projects",
    description: "Group intent, knowledge, and resources under a durable work context.",
  },
  delivery: {
    eyebrow: "Routing intelligence",
    title: "Delivery",
    description: "Inspect every context decision, its input, rationale, and final delivery.",
  },
  context: {
    eyebrow: "Durable memory",
    title: "Context library",
    description: "Human intent and live references stored at stable logical addresses.",
  },
  memory: {
    eyebrow: "Memory governance",
    title: "Memory review",
    description: "Approve global writes, resolve evidence conflicts, and audit project memory changes.",
  },
  sessions: {
    eyebrow: "Exact provenance",
    title: "Sessions",
    description: "A content-addressed record of the context delivered to every agent.",
  },
  requests: {
    eyebrow: "Human attention",
    title: "Context gaps",
    description: "Questions that need a durable answer before an agent can move with confidence.",
  },
  graph: {
    eyebrow: "Structural intelligence",
    title: "Code graph",
    description: "Explore code structure, dependencies, communities, and call relationships.",
  },
}

const emptyView: ViewResponse = {
  project: "",
  graphProject: "",
  graphProjects: [],
  resourceBinding: null,
  resources: [],
  topics: [],
  sessions: [],
  diagnostics: { database: "", integrity: "unknown", schemaVersion: 0, counts: {} },
}
const emptyRecall: Recall = { preferred: [], tentative: [], associations: [], activation: [] }
const emptyModelStatus: ModelStatus = {
  installed: false,
  running: false,
  ready: false,
  model: null,
  revision: null,
  runtime: null,
  endpoint: null,
  pid: null,
  startedAt: null,
  logPath: "",
  error: null,
  providerConfigured: false,
  providerSource: "none",
  providerModel: null,
}

const graphPalette = ["#3973a5", "#7559a2", "#287b72", "#97651b", "#5f7358"]

function relativeTime(timestamp: number) {
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - timestamp))
  if (seconds < 60) return "just now"
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

function shortSession(id: string) {
  return id.length > 24 ? `${id.slice(0, 11)}…${id.slice(-8)}` : id
}

function actionVariant(action: ContextAction) {
  if (action === "retrieve") return "success" as const
  if (action === "ask") return "warning" as const
  return "neutral" as const
}

type StatTone = "sage" | "blue" | "amber" | "violet" | "teal"

const statToneStyles: Record<StatTone, { card: string; icon: string; line: string }> = {
  sage: {
    card: "border-signal/25",
    icon: "border-signal/20 bg-signal-soft text-signal",
    line: "bg-signal",
  },
  blue: {
    card: "border-accent-blue/20",
    icon: "border-accent-blue/20 bg-accent-blue-soft text-accent-blue",
    line: "bg-accent-blue",
  },
  amber: {
    card: "border-accent-amber/20",
    icon: "border-accent-amber/20 bg-accent-amber-soft text-accent-amber",
    line: "bg-accent-amber",
  },
  violet: {
    card: "border-accent-violet/20",
    icon: "border-accent-violet/20 bg-accent-violet-soft text-accent-violet",
    line: "bg-accent-violet",
  },
  teal: {
    card: "border-accent-teal/20",
    icon: "border-accent-teal/20 bg-accent-teal-soft text-accent-teal",
    line: "bg-accent-teal",
  },
}

function StatCard({
  icon,
  label,
  value,
  detail,
  tone = "sage",
}: {
  icon: ReactNode
  label: string
  value: number
  detail: string
  tone?: StatTone
}) {
  const colors = statToneStyles[tone]
  return (
    <Card className={cn("group relative overflow-hidden", colors.card)}>
      <CardContent className="relative p-6">
        <div className="flex items-start justify-between gap-4">
          <span className="fine-label">{label}</span>
          <span
            className={cn(
              "grid size-9 place-items-center rounded-[11px] border [&_svg]:size-4",
              colors.icon,
            )}
          >
            {icon}
          </span>
        </div>
        <div className="mt-7 flex flex-col items-start gap-2 xl:mt-8 xl:flex-row xl:items-end xl:justify-between xl:gap-5">
          <strong className="mono-number text-[2.3rem] font-medium leading-none text-ink sm:text-[2.75rem]">{value}</strong>
          <span className="max-w-36 text-left text-[11px] leading-[1.45] text-muted xl:pb-1 xl:text-right">{detail}</span>
        </div>
        <span
          className={cn(
            "absolute bottom-0 left-6 h-px w-14 transition-all duration-300 group-hover:w-24",
            colors.line,
          )}
        />
      </CardContent>
    </Card>
  )
}

function TopicTable({ topics, onRefresh }: { topics: Topic[]; onRefresh: () => Promise<void> }) {
  const [search, setSearch] = useState("")
  const filtered = useMemo(() => {
    const query = search.toLowerCase()
    return topics.filter((topic) =>
      `${topic.key} ${topic.kind} ${topic.value ?? ""} ${topic.source ?? ""}`.toLowerCase().includes(query),
    )
  }, [search, topics])

  return (
    <Card>
      <CardHeader className="items-start xl:flex-row xl:items-center">
        <div>
          <div className="flex items-center gap-2">
            <CardTitle>Context library</CardTitle>
            <Badge variant="neutral">{filtered.length} items</Badge>
          </div>
          <CardDescription>
            Durable intent and references. Content is visible here so an address never loses its meaning.
          </CardDescription>
        </div>
        <div className="flex w-full gap-2 xl:w-auto">
          <div className="relative min-w-0 flex-1 xl:w-72">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-dim" />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search memory"
              className="pl-9"
            />
          </div>
          <CreateTopicDialog onCreated={onRefresh} />
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto px-0 pb-1">
        <table className="w-full min-w-[900px] text-left">
          <thead className="border-y border-line bg-panel-raised text-dim">
            <tr className="fine-label">
              <th className="w-[48%] px-6 py-3.5 font-semibold">Memory</th>
              <th className="px-4 py-3.5 font-semibold">Category</th>
              <th className="px-4 py-3.5 font-semibold">Authority</th>
              <th className="px-4 py-3.5 font-semibold">Updated</th>
              <th className="px-6 py-3.5 text-right font-semibold">Actions</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((topic) => (
              <tr key={topic.key} className="group border-b border-line/80 transition-colors hover:bg-signal/[0.025]">
                <td className="px-6 py-5 align-top">
                  <p className="font-mono text-xs font-semibold text-ink">{topic.key}</p>
                  <p className="mt-2 max-h-11 max-w-2xl overflow-hidden text-xs leading-[1.45rem] text-muted">
                    {topic.value ?? topic.source ?? "Empty context"}
                  </p>
                  {topic.value && topic.source && (
                    <p className="mt-1.5 font-mono text-[10px] text-dim">{topic.source}</p>
                  )}
                  <div className="mt-2 flex gap-2 text-[10px] text-dim">
                    <span>{topic.versionCount} versions</span>
                    <span>{topic.usage.selectedCount} selections</span>
                    <span>{topic.usage.expandedCount} expansions</span>
                    {topic.needsReviewCount > 0 && (
                      <span className="text-amber-800">{topic.needsReviewCount} needs review</span>
                    )}
                  </div>
                </td>
                <td className="px-4 py-5 align-top">
                  <Badge
                    variant={
                      topic.kind === "decision"
                        ? "violet"
                        : topic.kind === "code-area"
                          ? "teal"
                          : topic.kind === "doc-ref"
                            ? "warning"
                            : "blue"
                    }
                  >
                    {topic.category ?? "internal"}
                  </Badge>
                </td>
                <td className="px-4 py-5 align-top">
                  <Badge variant={topic.origin === "human" ? "success" : "default"}>{topic.origin}</Badge>
                </td>
                <td className="px-4 py-5 align-top">
                  <div className={cn("flex items-center gap-2 text-xs", topic.stale ? "text-amber-800" : "text-muted")}>
                    {topic.stale ? <AlertCircle className="size-3.5" /> : <Clock3 className="size-3.5" />}
                    {topic.stale ? "Review needed" : relativeTime(topic.set_at)}
                  </div>
                </td>
                <td className="px-6 py-5 align-top">
                  <div className="flex justify-end gap-1">
                    <TopicHistoryDialog topic={topic} />
                    {topic.stale && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={async () => {
                          await confirmTopic(topic.key)
                          await onRefresh()
                        }}
                      >
                        <Check /> Confirm
                      </Button>
                    )}
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${topic.key}`}
                      onClick={async () => {
                        if (window.confirm(`Delete ${topic.key}?`)) {
                          await deleteTopic(topic.key)
                          await onRefresh()
                        }
                      }}
                    >
                      <Trash2 className="text-dim" />
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!filtered.length && (
          <div className="px-6 py-16 text-center text-sm text-muted">No context matches this search.</div>
        )}
      </CardContent>
    </Card>
  )
}

function TopicHistoryDialog({ topic }: { topic: Topic }) {
  const [open, setOpen] = useState(false)
  const [versions, setVersions] = useState<MemoryVersion[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  async function onOpenChange(nextOpen: boolean) {
    setOpen(nextOpen)
    if (!nextOpen) return
    setLoading(true)
    setError("")
    try {
      setVersions(await getMemoryVersions(topic.key))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load memory versions")
    } finally {
      setLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => void onOpenChange(nextOpen)}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="icon" aria-label={`View versions for ${topic.key}`}>
          <History />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Memory versions</DialogTitle>
          <DialogDescription>
            Current content and up to two superseded versions for <span className="font-mono">{topic.key}</span>.
          </DialogDescription>
        </DialogHeader>
        {loading && <p className="py-8 text-center text-sm text-muted">Loading versions…</p>}
        {error && <p className="text-sm text-red-700">{error}</p>}
        {!loading && !error && (
          <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
            {versions.map((version) => (
              <article key={version.id} className="surface-row rounded-[14px] p-4">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <Badge variant={version.current ? "success" : "neutral"}>
                      v{version.version} · {version.current ? "current" : "superseded"}
                    </Badge>
                    <span className="text-[10px] text-dim">{relativeTime(version.createdAt)}</span>
                  </div>
                  <span className="font-mono text-[10px] text-dim">{version.contentHash.slice(0, 12)}</span>
                </div>
                <pre className="mt-3 whitespace-pre-wrap break-words text-xs leading-6 text-muted">
                  {version.value ?? version.source ?? "Empty context"}
                </pre>
              </article>
            ))}
            {!versions.length && (
              <EmptyState icon={<History />} title="No versions" detail="The first applied write will create version one." />
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function CreateTopicDialog({ onCreated }: { onCreated: () => Promise<void> }) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<"inline" | "pointer">("inline")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setPending(true)
    setError("")
    try {
      await createTopic({
        key: String(data.get("key") ?? ""),
        kind: String(data.get("kind") ?? "note"),
        ...(mode === "inline"
          ? { value: String(data.get("value") ?? "") }
          : { source: String(data.get("source") ?? "") }),
      })
      setOpen(false)
      await onCreated()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create context")
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button><Plus /> Add context</Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create durable context</DialogTitle>
          <DialogDescription>
            Give this memory a stable address. Human-authored context remains protected from graph reseeding.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <label className="block space-y-2 text-xs font-medium text-muted">
            Logical address
            <Input name="key" required placeholder="intent.product.simplicity" />
          </label>
          <label className="block space-y-2 text-xs font-medium text-muted">
            Type
            <select
              name="kind"
              className="h-10 w-full rounded-[10px] border border-line-strong bg-panel-raised px-3 text-sm text-ink outline-none"
            >
              <option value="decision">Intent</option>
              <option value="note">Knowledge</option>
              <option value="doc-ref">Reference</option>
            </select>
          </label>
          <div className="grid grid-cols-2 gap-1 rounded-[11px] border border-line bg-canvas p-1">
            {(["inline", "pointer"] as const).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setMode(item)}
                className={cn(
                  "rounded-lg px-3 py-2 text-xs font-semibold capitalize transition",
                  mode === item ? "bg-panel-raised text-ink" : "text-dim hover:text-muted",
                )}
              >
                {item}
              </button>
            ))}
          </div>
          {mode === "inline" ? (
            <label className="block space-y-2 text-xs font-medium text-muted">
              Context
              <Textarea name="value" required placeholder="Keep the product simple because…" />
            </label>
          ) : (
            <label className="block space-y-2 text-xs font-medium text-muted">
              Live pointer
              <Input name="source" required placeholder="@repo/src/auth" />
            </label>
          )}
          {error && <p className="text-xs text-red-700">{error}</p>}
          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button disabled={pending}>
              {pending ? <RefreshCw className="animate-spin" /> : <Plus />}
              {pending ? "Saving" : "Create"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SessionList({ sessions }: { sessions: Session[] }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Session delivery</CardTitle>
          <CardDescription>Exactly what each agent received, pinned to its content hash.</CardDescription>
        </div>
        <Badge variant="success"><CircleDot className="mr-1 size-2.5" /> Live</Badge>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {sessions.map((session) => {
            const latest = session.items.length
              ? Math.max(...session.items.map((item) => item.deliveredAt))
              : null
            return (
              <article key={session.id} className="surface-row rounded-[13px] px-4 py-4">
                <div className="flex items-start justify-between gap-5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="size-1.5 shrink-0 rounded-full bg-positive" />
                      <p className="truncate font-mono text-xs font-medium text-ink">{shortSession(session.id)}</p>
                    </div>
                    <p className="ml-3.5 mt-1.5 text-[11px] text-muted">
                      {session.project ?? "Unscoped session"} · {session.items.length} delivered
                    </p>
                  </div>
                  <time className="shrink-0 text-[11px] text-dim">{latest ? relativeTime(latest) : "No deliveries"}</time>
                </div>
                {!!session.items.length && (
                  <div className="mt-3.5 flex flex-wrap gap-1.5 border-t border-line/70 pt-3">
                    {session.items.slice(0, 8).map((item) => (
                      <span
                        key={item.key}
                        title={item.valueHash}
                        className="rounded-md bg-signal-soft px-2 py-1 font-mono text-[10px] text-signal"
                      >
                        {item.key}
                      </span>
                    ))}
                    {session.items.length > 8 && (
                      <span className="px-2 py-1 text-[10px] text-dim">+{session.items.length - 8} more</span>
                    )}
                  </div>
                )}
              </article>
            )
          })}
        </div>
        {!sessions.length && (
          <EmptyState
            icon={<Users />}
            title="No agent sessions yet"
            detail="Sessions appear after Purpory prepares context for an agent."
          />
        )}
      </CardContent>
    </Card>
  )
}

function RecallPanel({ recall }: { recall: Recall }) {
  const items = [
    ...recall.preferred.map((item) => ({ ...item, tier: "preferred" })),
    ...recall.tentative.map((item) => ({ ...item, tier: "tentative" })),
  ].slice(0, 6)

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Emerging memory</CardTitle>
          <CardDescription>Signals strengthened by reuse across sessions.</CardDescription>
        </div>
        <BrainCircuit className="size-4 text-accent-violet" />
      </CardHeader>
      <CardContent>
        <div className="space-y-1">
          {items.map((item, index) => (
            <div key={item.key} className="flex items-center gap-3 rounded-[10px] px-2 py-2.5 hover:bg-black/[0.025]">
              <span className="mono-number w-5 text-[10px] text-dim">{String(index + 1).padStart(2, "0")}</span>
              <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-ink">{item.key}</span>
              <span className="text-[10px] tabular-nums text-dim">{item.score?.toFixed(2)}</span>
              <Badge variant={item.tier === "preferred" ? "success" : "neutral"}>{item.tier}</Badge>
            </div>
          ))}
        </div>
        {!items.length && (
          <EmptyState
            icon={<Sparkles />}
            title="Memory is quiet"
            detail="Recall signals appear after context is reused across sessions."
          />
        )}
      </CardContent>
    </Card>
  )
}

function RequestQueue({
  requests,
  topics,
  onRefresh,
}: {
  requests: ContextRequest[]
  topics: Topic[]
  onRefresh: () => Promise<void>
}) {
  const open = requests.filter((request) => request.status === "open")
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Context gaps</CardTitle>
          <CardDescription>Agent questions waiting for a durable human answer.</CardDescription>
        </div>
        <Badge variant={open.length ? "warning" : "success"}>{open.length} open</Badge>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {requests.map((request) => (
            <RequestItem key={request.id} request={request} topics={topics} onRefresh={onRefresh} />
          ))}
        </div>
        {!requests.length && (
          <EmptyState icon={<ShieldCheck />} title="No context gaps" detail="Unanswered agent needs will collect here." />
        )}
      </CardContent>
    </Card>
  )
}

function RequestItem({
  request,
  topics,
  onRefresh,
}: {
  request: ContextRequest
  topics: Topic[]
  onRefresh: () => Promise<void>
}) {
  const [key, setKey] = useState(topics[0]?.key ?? "")
  useEffect(() => {
    if (!key && topics[0]) setKey(topics[0].key)
  }, [key, topics])

  return (
    <article className="surface-row rounded-[13px] p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-sm leading-6 text-ink">{request.need}</p>
          <p className="mt-1.5 font-mono text-[10px] text-dim">
            {shortSession(request.sessionId)} · {relativeTime(request.createdAt)}
          </p>
        </div>
        <Badge variant={request.status === "open" ? "warning" : "success"}>{request.status}</Badge>
      </div>
      {request.status === "open" ? (
        <div className="mt-4 flex gap-2 border-t border-line/70 pt-3">
          <select
            value={key}
            onChange={(event) => setKey(event.target.value)}
            className="h-9 min-w-0 flex-1 rounded-lg border border-line-strong bg-panel-raised px-2 font-mono text-[11px] text-ink outline-none"
          >
            {topics.map((topic) => <option key={topic.key}>{topic.key}</option>)}
          </select>
          <Button
            size="sm"
            disabled={!key}
            onClick={async () => {
              await resolveRequest(request.id, key)
              await onRefresh()
            }}
          >
            Resolve <ArrowRight />
          </Button>
        </div>
      ) : (
        <div className="mt-3 flex items-center gap-2 border-t border-line/70 pt-3 font-mono text-[11px] text-positive">
          <Check className="size-3.5" /> {request.resolvedKey}
        </div>
      )}
    </article>
  )
}

function PreparationPanel({
  project,
  decisions,
  modelStatus,
  onRefresh,
}: {
  project: string
  decisions: ContextDecision[]
  modelStatus: ModelStatus
  onRefresh: () => Promise<void>
}) {
  const [message, setMessage] = useState("")
  const [retainInput, setRetainInput] = useState(true)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")
  const [result, setResult] = useState<ContextPreparation | null>(null)
  const [showAllDecisions, setShowAllDecisions] = useState(false)
  const [previewSessionId] = useState(() => `dashboard:${crypto.randomUUID()}`)

  const fallbackCount = decisions.filter((decision) => decision.fallback).length
  const correctedCount = decisions.filter((decision) => decision.feedback?.verdict === "incorrect").length
  const attentionDecisions = decisions.filter((decision) =>
    !decision.feedback
    && (
      decision.action === "ask"
      || Boolean(decision.fallback)
    ),
  )
  const visibleDecisions = showAllDecisions ? decisions : attentionDecisions
  const latencies = decisions.flatMap((decision) => decision.latencyMs === null ? [] : [decision.latencyMs])
  const averageLatency = latencies.length
    ? Math.round(latencies.reduce((sum, value) => sum + value, 0) / latencies.length)
    : 0

  async function evaluate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPending(true)
    setError("")
    try {
      const next = await prepareContext({
        message,
        sessionId: previewSessionId,
        project,
        tokenBudget: 2_000,
        retainInput,
      })
      setResult(next)
      await onRefresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not evaluate the request")
    } finally {
      setPending(false)
    }
  }

  async function feedback(decision: ContextDecision, expected: ContextAction) {
    await submitContextFeedback(decision.id, {
      verdict: expected === decision.action ? "correct" : "incorrect",
      ...(expected === decision.action ? {} : { expectedAction: expected }),
    })
    await onRefresh()
  }

  return (
    <div className="space-y-5">
      <Card className="overflow-hidden">
        <CardContent className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <div
              className={cn(
                "relative grid size-11 place-items-center rounded-[13px] border",
                modelStatus.ready
                  ? "border-positive/20 bg-positive-soft text-positive"
                  : modelStatus.running
                    ? "border-amber-700/20 bg-amber-50 text-amber-800"
                    : "border-line bg-panel-raised text-dim",
              )}
            >
              <BrainCircuit className="size-5" />
              <span
                className={cn(
                  "absolute -right-0.5 -top-0.5 size-2.5 rounded-full border-2 border-panel",
                  modelStatus.ready ? "bg-positive" : modelStatus.running ? "bg-amber-300" : "bg-dim",
                )}
              />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-ink">Local routing model</p>
                <Badge variant={modelStatus.ready ? "success" : modelStatus.running ? "warning" : "neutral"}>
                  {modelStatus.ready ? "Ready" : modelStatus.running ? "Starting" : modelStatus.installed ? "Stopped" : "Not installed"}
                </Badge>
              </div>
              <p className="mt-1.5 font-mono text-[10px] text-dim">
                {modelStatus.providerModel ?? modelStatus.model ?? "Qwen/Qwen3.5-0.8B"}
                {modelStatus.revision ? ` @ ${modelStatus.revision.slice(0, 12)}` : ""}
              </p>
            </div>
          </div>
          <div className="border-l-2 border-line pl-4 sm:text-right">
            <p className="text-[11px] text-muted">Provider · {modelStatus.providerSource}</p>
            <p className="mt-1 max-w-md truncate font-mono text-[10px] text-dim">
              {modelStatus.endpoint ?? "purpory model install && purpory model start"}
            </p>
          </div>
        </CardContent>
      </Card>

      <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <StatCard icon={<Route />} label="Review queue" value={attentionDecisions.length} detail="exceptions needing attention" tone="blue" />
        <StatCard icon={<RefreshCw />} label="Fallbacks" value={fallbackCount} detail="model unavailable or invalid" tone="amber" />
        <StatCard icon={<AlertCircle />} label="Corrections" value={correctedCount} detail="human-labelled mistakes" tone="violet" />
        <StatCard icon={<Clock3 />} label="Latency" value={averageLatency} detail="average milliseconds" tone="teal" />
      </section>

      <section className="grid items-start gap-5 xl:grid-cols-[minmax(320px,.7fr)_minmax(560px,1.3fr)]">
        <Card className="xl:sticky xl:top-[118px]">
          <CardHeader>
            <div>
              <CardTitle>Prepare context</CardTitle>
              <CardDescription>Preview what Purpory would deliver before an agent sees it.</CardDescription>
            </div>
            <Route className="size-4 text-accent-blue" />
          </CardHeader>
          <CardContent>
            <form onSubmit={evaluate} className="space-y-4">
              <Textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                required
                placeholder="전에 정한 인증 정책을 알려줘"
                className="min-h-32"
              />
              <label className="flex cursor-pointer items-start gap-3 rounded-[11px] border border-line bg-canvas/50 p-3 text-xs leading-5 text-muted">
                <input
                  type="checkbox"
                  checked={retainInput}
                  onChange={(event) => setRetainInput(event.target.checked)}
                  className="mt-0.5 size-4 accent-[#5f7358]"
                />
                <span>
                  <strong className="block font-semibold text-ink">Retain this local input</strong>
                  Store the text so future routing decisions can be inspected and evaluated.
                </span>
              </label>
              {error && <p className="text-xs text-red-700">{error}</p>}
              <Button className="w-full" disabled={pending || !message.trim()}>
                {pending ? <RefreshCw className="animate-spin" /> : <Send />}
                {pending ? "Preparing" : "Prepare context"}
              </Button>
            </form>

            {result && (
              <div className="mt-5 rounded-[13px] border border-signal/15 bg-signal/[0.035] p-4" aria-live="polite">
                <div className="flex items-center justify-between">
                  <Badge variant={actionVariant(result.action)}>{result.action}</Badge>
                  <span className="font-mono text-[10px] text-dim">Decision #{result.decisionId}</span>
                </div>
                <p className="mt-3 text-xs leading-5 text-muted">
                  {result.delivery.length} delivered · {result.omitted.length} omitted ·{" "}
                  {result.fallback ? "deterministic fallback" : `model ${result.model.id ?? "unknown"}`}
                </p>
                {result.clarification && (
                  <p className="mt-3 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-900">
                    {result.clarification}
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Decision audit</CardTitle>
              <CardDescription>
                Review exceptions by default. Routine decisions stay available without requiring a label.
              </CardDescription>
            </div>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setShowAllDecisions((current) => !current)}
            >
              {showAllDecisions ? "Show review queue" : `Show all ${decisions.length}`}
            </Button>
          </CardHeader>
          <CardContent>
            <div className="space-y-2.5">
              {visibleDecisions.slice(0, 20).map((decision) => (
                <article key={decision.id} className="surface-row rounded-[14px] p-5">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <Badge variant={actionVariant(decision.action)}>{decision.action}</Badge>
                      <span className="font-mono text-[10px] text-dim">#{decision.id}</span>
                      <span className="text-line-strong">/</span>
                      <span className="truncate font-mono text-[10px] text-dim">{shortSession(decision.sessionId)}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <time className="text-[10px] text-dim">{relativeTime(decision.createdAt)}</time>
                      {decision.fallback && <Badge variant="warning">Fallback</Badge>}
                    </div>
                  </div>

                  <div className="mt-4 border-l-2 border-signal/40 pl-4">
                    <p className="fine-label mb-2">{decision.inputText ? "Input" : "Private input"}</p>
                    <p className={cn(
                      "break-words leading-6",
                      decision.inputText ? "text-[15px] text-ink" : "font-mono text-xs text-muted",
                    )}>
                      {decision.inputText ?? `Hash ${decision.inputHash.slice(0, 16)}`}
                    </p>
                  </div>

                  <div className="mt-4 grid gap-2 rounded-[10px] bg-canvas/55 px-3.5 py-3 text-[10px] text-muted sm:grid-cols-3">
                    <div>
                      <span className="fine-label block">Reason</span>
                      <span className="mt-1.5 block font-mono text-ink">{decision.proposal.reasonCode}</span>
                    </div>
                    <div>
                      <span className="fine-label block">Delivery</span>
                      <span className="mt-1.5 block text-ink">{decision.delivery.length} context items</span>
                    </div>
                    <div>
                      <span className="fine-label block">Route</span>
                      <span className="mt-1.5 block truncate text-ink">
                        {decision.fallback ? "Deterministic fallback" : decision.model.id ?? "Model"}
                      </span>
                    </div>
                  </div>

                  <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-line/70 pt-3">
                    <span className="fine-label mr-2">Optional review</span>
                    {!decision.feedback && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void feedback(decision, decision.action)}
                      >
                        Looks right
                      </Button>
                    )}
                    {(["skip", "retrieve", "ask"] as ContextAction[])
                      .filter((action) => action !== decision.action)
                      .map((action) => {
                      const selected =
                        decision.feedback?.expectedAction === action
                      return (
                        <Button
                          key={action}
                          variant={selected ? "secondary" : "ghost"}
                          size="sm"
                          aria-pressed={selected}
                          onClick={() => void feedback(decision, action)}
                          className={cn(selected && "border-signal/25 text-signal")}
                        >
                          Should {action}
                        </Button>
                      )
                    })}
                    {decision.feedback && (
                      <Badge
                        className="ml-auto"
                        variant={decision.feedback.verdict === "correct" ? "success" : "warning"}
                      >
                        {decision.feedback.verdict}
                      </Badge>
                    )}
                  </div>
                </article>
              ))}
            </div>
            {!visibleDecisions.length && (
              <EmptyState
                icon={<ShieldCheck />}
                title={decisions.length ? "Review queue is clear" : "No context decisions"}
                detail={
                  decisions.length
                    ? "Routine decisions remain in the full audit and do not require evaluation."
                    : "Prepare a request or connect an agent to build the audit trail."
                }
              />
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  )
}

function GraphPanel() {
  const [scope, setScope] = useState("")
  const [graph, setGraph] = useState<GraphPayload | null>(null)
  const [pending, setPending] = useState(true)
  const [previewError, setPreviewError] = useState("")

  const loadGraph = useCallback(async (selectedScope?: string) => {
    setPending(true)
    setPreviewError("")
    try {
      setGraph(await getGraph(selectedScope || undefined))
    } catch (caught) {
      setPreviewError(caught instanceof Error ? caught.message : "Could not load context graph")
    } finally {
      setPending(false)
    }
  }, [])

  useEffect(() => {
    void loadGraph()
  }, [loadGraph])

  const nodes = graph?.nodes.slice(0, 28) ?? []
  const positions = useMemo(
    () =>
      new Map(
        nodes.map((node, index) => [
          String(node.id),
          {
            x: 200 + Math.cos((index / Math.max(nodes.length, 1)) * Math.PI * 2) * (96 + (index % 3) * 21),
            y: 160 + Math.sin((index / Math.max(nodes.length, 1)) * Math.PI * 2) * (86 + (index % 2) * 20),
          },
        ]),
      ),
    [nodes],
  )
  const visibleLinks = useMemo(
    () =>
      (graph?.links ?? [])
        .filter((link) => positions.has(String(link.source)) && positions.has(String(link.target)))
        .slice(0, 72),
    [graph, positions],
  )

  return (
    <Card className="flex min-h-[740px] flex-col overflow-hidden">
      <CardHeader className="border-b border-line pb-5">
        <div>
          <CardTitle>Code graph</CardTitle>
          <CardDescription>Structural relationships from the canonical context graph.</CardDescription>
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col p-0">
        <div className="flex flex-1 flex-col gap-4 p-5">
            <div className="flex gap-2">
              <Input
                value={scope}
                onChange={(event) => setScope(event.target.value)}
                placeholder="Optional scope, e.g. src/auth"
              />
              <Button onClick={() => void loadGraph(scope)} disabled={pending}>
                {pending ? <RefreshCw className="animate-spin" /> : <GitFork />}
                {pending ? "Loading" : "Load graph"}
              </Button>
            </div>
            {previewError && <p className="text-xs text-red-700">{previewError}</p>}

            <div className="flex min-h-[520px] flex-1">
              {pending && !graph ? (
                <div className="flex flex-1 items-center justify-center gap-3 text-xs text-muted">
                  <RefreshCw className="size-5 animate-spin text-accent-blue" /> Loading context graph…
                </div>
              ) : graph && graph.nodes.length ? (
                <div className="relative flex-1 overflow-hidden rounded-[14px] border border-line bg-panel">
                  <div className="absolute left-4 top-4 z-10 flex gap-2">
                    <Badge variant="success">{graph.totalNodes} nodes</Badge>
                    <Badge variant="neutral">{graph.totalLinks} edges</Badge>
                  </div>
                  <svg viewBox="0 0 400 320" className="h-full w-full">
                    <defs>
                      <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
                        <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#dce1d8" strokeWidth=".45" />
                      </pattern>
                    </defs>
                    <rect width="400" height="320" fill="url(#grid)" />
                    {visibleLinks.map((link, index) => {
                      const source = positions.get(String(link.source))
                      const target = positions.get(String(link.target))
                      return source && target ? (
                        <line
                          key={`${link.source}-${link.target}-${index}`}
                          x1={source.x}
                          y1={source.y}
                          x2={target.x}
                          y2={target.y}
                          stroke="#9aa696"
                          strokeOpacity=".55"
                          strokeWidth=".65"
                        />
                      ) : null
                    })}
                    {nodes.map((node, index) => {
                      const position = positions.get(String(node.id))!
                      const communitySeed = String(node.community ?? index)
                        .split("")
                        .reduce((sum, character) => sum + character.charCodeAt(0), 0)
                      const color = graphPalette[communitySeed % graphPalette.length]
                      return (
                        <g key={String(node.id)}>
                          <circle cx={position.x} cy={position.y} r="4.5" fill={color} />
                          <circle cx={position.x} cy={position.y} r="8" fill="none" stroke={color} strokeOpacity=".2" />
                          <text x={position.x + 9} y={position.y + 3} fill="#4e584b" fontSize="6.8">
                            {(node.label ?? node.id).slice(0, 24)}
                          </text>
                        </g>
                      )
                    })}
                    <text x="14" y="304" fill="#6f786c" fontSize="7.5">
                      showing {nodes.length} of {graph.totalNodes} nodes · {visibleLinks.length} of {graph.totalLinks} edges
                    </text>
                  </svg>
                </div>
              ) : (
                <div className="flex flex-1 items-center justify-center">
                  <EmptyState
                    icon={<Network />}
                    title="No structural context found"
                    detail="Run purpory update . or choose a scope containing indexed source files."
                  />
                </div>
              )}
            </div>
        </div>
      </CardContent>
    </Card>
  )
}

function GlobalMemoryRequestCard({
  request,
  onRefresh,
}: {
  request: GlobalMemoryRequest
  onRefresh: () => Promise<void>
}) {
  const [key, setKey] = useState(request.proposal.key)
  const [kind, setKind] = useState(request.proposal.kind)
  const [rationale, setRationale] = useState(request.proposal.rationale)
  const [mode, setMode] = useState<"inline" | "pointer">(request.proposal.source ? "pointer" : "inline")
  const [content, setContent] = useState(request.proposal.value ?? request.proposal.source ?? "")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")

  async function persist(decision?: "approve" | "reject") {
    setPending(true)
    setError("")
    try {
      await editGlobalMemoryRequest(request.id, {
        key,
        kind,
        rationale,
        ...(mode === "inline" ? { value: content } : { source: content }),
      })
      if (decision) await decideGlobalMemoryRequest(request.id, decision)
      await onRefresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update global memory request")
    } finally {
      setPending(false)
    }
  }

  return (
    <article className="surface-row rounded-[14px] p-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <Badge variant="warning">Pending #{request.id}</Badge>
            <span className="text-[10px] text-dim">{relativeTime(request.createdAt)}</span>
          </div>
          <p className="mt-3 text-[11px] text-muted">
            Initial: <span className="font-mono text-ink">{request.initialProposal.key}</span>
            {" · "}{request.initialProposal.value ?? request.initialProposal.source}
          </p>
        </div>
        <Globe2 className="size-4 text-signal" />
      </div>
      <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_10rem]">
        <Input value={key} onChange={(event) => setKey(event.target.value)} aria-label="Global memory key" />
        <select
          value={kind}
          onChange={(event) => setKind(event.target.value as GlobalMemoryRequest["proposal"]["kind"])}
          className="h-10 rounded-[10px] border border-line-strong bg-panel-raised px-3 text-sm text-ink outline-none"
        >
          <option value="decision">Intent</option>
          <option value="note">Knowledge</option>
          <option value="doc-ref">Reference</option>
        </select>
      </div>
      <div className="mt-3 flex gap-1">
        {(["inline", "pointer"] as const).map((item) => (
          <Button
            key={item}
            type="button"
            variant={mode === item ? "secondary" : "ghost"}
            size="sm"
            onClick={() => setMode(item)}
          >
            {item}
          </Button>
        ))}
      </div>
      <Textarea
        className="mt-3"
        value={content}
        onChange={(event) => setContent(event.target.value)}
        aria-label="Proposed global memory content"
      />
      <Textarea
        className="mt-3"
        value={rationale}
        onChange={(event) => setRationale(event.target.value)}
        aria-label="Global memory rationale"
      />
      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <Button variant="secondary" disabled={pending} onClick={() => void persist()}>
          <Save /> Save edits
        </Button>
        <Button variant="ghost" disabled={pending} onClick={() => void persist("reject")}>
          Reject
        </Button>
        <Button disabled={pending} onClick={() => void persist("approve")}>
          <Check /> Approve final
        </Button>
      </div>
    </article>
  )
}

function NeedsReviewCard({
  review,
  topic,
  onRefresh,
}: {
  review: NeedsReview
  topic: Topic | undefined
  onRefresh: () => Promise<void>
}) {
  const [content, setContent] = useState(topic?.value ?? topic?.source ?? "")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")

  async function resolve(outcome: "keep" | "change") {
    setPending(true)
    setError("")
    try {
      await resolveNeedsReview(review.id, {
        outcome,
        ...(outcome === "change" && topic
          ? {
              change: {
                key: review.key,
                kind: topic.kind,
                ...(topic.source ? { source: content } : { value: content }),
              },
            }
          : {}),
      })
      await onRefresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not resolve review")
    } finally {
      setPending(false)
    }
  }

  return (
    <article className="surface-row rounded-[14px] p-5">
      <div className="flex items-center gap-2">
        <Badge variant="warning">Needs review</Badge>
        <span className="font-mono text-xs text-ink">{review.key}</span>
      </div>
      <p className="mt-3 text-sm leading-6 text-muted">{review.reason}</p>
      <p className="mt-2 font-mono text-[10px] text-dim">
        {review.sourceType}:{review.sourceId} · {review.contentHash.slice(0, 12)}
      </p>
      {topic && (
        <Textarea
          className="mt-4"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          aria-label={`Reviewed memory ${review.key}`}
        />
      )}
      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" disabled={pending} onClick={() => void resolve("keep")}>
          Keep current
        </Button>
        <Button disabled={pending || !topic} onClick={() => void resolve("change")}>
          Apply change
        </Button>
      </div>
    </article>
  )
}

function MemoryGovernancePanel({
  globalRequests,
  reviews,
  report,
  topics,
  onRefresh,
}: {
  globalRequests: GlobalMemoryRequest[]
  reviews: NeedsReview[]
  report: MemoryReportDay[]
  topics: Topic[]
  onRefresh: () => Promise<void>
}) {
  const pendingGlobal = globalRequests.filter((request) => request.status === "pending")
  const decidedGlobal = globalRequests.filter((request) => request.status !== "pending")
  const openReviews = reviews.filter((review) => review.status === "open")
  const resolvedReviews = reviews.filter((review) => review.status === "resolved")
  return (
    <div className="grid items-start gap-5 xl:grid-cols-2">
      <div className="space-y-5">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Global memory approvals</CardTitle>
              <CardDescription>Inspect every field, edit the proposal, then approve or reject it.</CardDescription>
            </div>
            <Badge variant="warning">{pendingGlobal.length} pending</Badge>
          </CardHeader>
          <CardContent className="space-y-3">
            {pendingGlobal.map((request) => (
              <GlobalMemoryRequestCard key={request.id} request={request} onRefresh={onRefresh} />
            ))}
            {!pendingGlobal.length && (
              <EmptyState icon={<Globe2 />} title="No pending global writes" detail="Global memory remains unchanged until an explicit approval request arrives." />
            )}
            {decidedGlobal.length > 0 && (
              <section className="border-t border-line pt-4">
                <p className="fine-label mb-3">Decision history</p>
                <div className="space-y-2">
                  {decidedGlobal.map((request) => (
                    <article key={request.id} className="surface-row rounded-[12px] p-4">
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2">
                          <Badge variant={request.status === "approved" ? "success" : "neutral"}>
                            {request.status} #{request.id}
                          </Badge>
                          <span className="font-mono text-[11px] text-ink">
                            {(request.finalProposal ?? request.proposal).key}
                          </span>
                        </div>
                        <span className="text-[10px] text-dim">
                          {request.decidedAt ? relativeTime(request.decidedAt) : ""}
                        </span>
                      </div>
                      <p className="mt-2 text-xs leading-5 text-muted">
                        {(request.finalProposal ?? request.proposal).value
                          ?? (request.finalProposal ?? request.proposal).source}
                      </p>
                      <details className="mt-2 text-[10px] text-dim">
                        <summary className="cursor-pointer">Initial and final proposal</summary>
                        <pre className="mt-2 overflow-x-auto whitespace-pre-wrap">
                          {JSON.stringify({
                            initial: request.initialProposal,
                            final: request.finalProposal,
                          }, null, 2)}
                        </pre>
                      </details>
                    </article>
                  ))}
                </div>
              </section>
            )}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Evidence conflicts</CardTitle>
              <CardDescription>Keep the current intent or commit a revised version.</CardDescription>
            </div>
            <Badge variant="warning">{openReviews.length} open</Badge>
          </CardHeader>
          <CardContent className="space-y-3">
            {openReviews.map((review) => (
              <NeedsReviewCard
                key={review.id}
                review={review}
                topic={topics.find((topic) => topic.key === review.key)}
                onRefresh={onRefresh}
              />
            ))}
            {!openReviews.length && (
              <EmptyState icon={<ShieldCheck />} title="No conflicts" detail="Changed evidence will create a new review without overwriting memory." />
            )}
            {resolvedReviews.length > 0 && (
              <section className="border-t border-line pt-4">
                <p className="fine-label mb-3">Review history</p>
                <div className="space-y-2">
                  {resolvedReviews.map((review) => (
                    <article key={review.id} className="surface-row rounded-[12px] p-4">
                      <div className="flex items-center gap-2">
                        <Badge variant={review.outcome === "change" ? "blue" : "neutral"}>
                          {review.outcome === "change" ? "Changed" : "Kept"}
                        </Badge>
                        <span className="font-mono text-[11px] text-ink">{review.key}</span>
                      </div>
                      <p className="mt-2 text-xs text-muted">{review.reason}</p>
                      <p className="mt-2 font-mono text-[10px] text-dim">
                        {review.sourceType}:{review.sourceId} · {review.contentHash.slice(0, 12)}
                        {review.resultVersionId ? ` · version ${review.resultVersionId}` : ""}
                      </p>
                    </article>
                  ))}
                </div>
              </section>
            )}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Project memory report</CardTitle>
            <CardDescription>All project-memory changes grouped by local date.</CardDescription>
          </div>
          <History className="size-4 text-signal" />
        </CardHeader>
        <CardContent className="space-y-5">
          {report.map((day) => (
            <section key={day.date}>
              <p className="fine-label mb-2">{day.date}</p>
              <div className="space-y-2">
                {day.events.map((event) => (
                  <article key={event.id} className="surface-row rounded-[12px] p-4">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-mono text-[11px] text-ink">{event.type}</span>
                      <span className="text-[10px] text-dim">{relativeTime(event.occurredAt)}</span>
                    </div>
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-[10px] leading-5 text-muted">
                      {JSON.stringify(event.payload, null, 2)}
                    </pre>
                  </article>
                ))}
              </div>
            </section>
          ))}
          {!report.length && (
            <EmptyState icon={<History />} title="No memory changes" detail="Applied project reconciliations will appear here by date." />
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function GitResourceForm({
  project,
  onRefresh,
}: {
  project: ProjectNamespace
  onRefresh: () => Promise<void>
}) {
  const [path, setPath] = useState("")
  const [alias, setAlias] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")

  async function attach(event: FormEvent) {
    event.preventDefault()
    setPending(true)
    setError("")
    try {
      await attachGitResource(project.id, {
        path,
        ...(alias.trim() ? { alias } : {}),
      })
      setPath("")
      setAlias("")
      await onRefresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not attach Git resource")
    } finally {
      setPending(false)
    }
  }

  return (
    <form onSubmit={attach} className="mt-4 rounded-[12px] border border-line bg-canvas/60 p-4">
      <div className="flex items-center gap-2">
        <GitFork className="size-4 text-signal" />
        <p className="text-xs font-semibold text-ink">Attach Git resource</p>
        <Badge variant="neutral">Provider</Badge>
      </div>
      <p className="mt-1.5 text-[11px] leading-5 text-muted">
        Enter a Git remote URL or a local checkout path. Remote URLs register the Resource; local checkouts also discover its worktree Views.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-[minmax(0,1fr)_12rem_auto]">
        <Input
          value={path}
          onChange={(event) => setPath(event.target.value)}
          placeholder="https://github.com/org/repo or /path/to/checkout"
          aria-label={`Git checkout path for ${project.name}`}
          required
        />
        <Input
          value={alias}
          onChange={(event) => setAlias(event.target.value)}
          placeholder="Alias (optional)"
          aria-label={`Resource alias for ${project.name}`}
        />
        <Button type="submit" disabled={pending || !path.trim()}>
          {pending ? <RefreshCw className="animate-spin" /> : <Plus />}
          Attach
        </Button>
      </div>
      {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
    </form>
  )
}

function ProjectPanel({
  projects,
  activeProject,
  activeView,
  onRefresh,
}: {
  projects: ProjectNamespace[]
  activeProject: string
  activeView: string
  onRefresh: () => Promise<void>
}) {
  const [name, setName] = useState("")
  const [description, setDescription] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState("")

  async function create(event: FormEvent) {
    event.preventDefault()
    setPending(true)
    setError("")
    try {
      await createProject({ name, description })
      setName("")
      setDescription("")
      await onRefresh()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create project")
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Create a project context</CardTitle>
            <CardDescription>
              A project is a durable namespace for intent and knowledge. Resources provide the changing
              material an agent works with.
            </CardDescription>
          </div>
          <FolderKanban className="size-5 text-signal" />
        </CardHeader>
        <CardContent>
          <form onSubmit={create} className="grid gap-3 lg:grid-cols-[18rem_minmax(0,1fr)_auto]">
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Project name"
              aria-label="Project name"
              required
            />
            <Input
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="What work belongs in this context?"
              aria-label="Project description"
            />
            <Button type="submit" disabled={pending || !name.trim()}>
              {pending ? <RefreshCw className="animate-spin" /> : <Plus />}
              Create project
            </Button>
          </form>
          {error && <p className="mt-2 text-xs text-red-700">{error}</p>}
        </CardContent>
      </Card>

      {projects.map((project) => {
        const resourceCount = project.resources.length
        const viewCount = project.resources.reduce((sum, resource) => sum + resource.views.length, 0)
        return (
          <Card key={project.id} className={cn(project.id === activeProject && "border-signal/35")}>
            <CardHeader>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <CardTitle>{project.name}</CardTitle>
                  {project.id === activeProject && <Badge variant="success">Active context</Badge>}
                  <Badge variant="neutral">{resourceCount} resources</Badge>
                  <Badge variant="neutral">{viewCount} views</Badge>
                </div>
                <CardDescription>
                  {project.description || "No description yet."}
                </CardDescription>
                <p className="mt-2 truncate font-mono text-[10px] text-dim">{project.id}</p>
              </div>
              <FolderKanban className="size-5 text-signal" />
            </CardHeader>
            <CardContent>
              <div className="space-y-3">
                {project.resources.map((resource) => (
                  <article key={resource.id} className="surface-row rounded-[14px] p-5">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <GitFork className="size-4 text-signal" />
                          <span className="text-sm font-semibold text-ink">
                            {resource.alias || resource.label}
                          </span>
                          <Badge variant="blue">{resource.provider}</Badge>
                          <Badge variant="neutral">{resource.kind}</Badge>
                        </div>
                        <p className="mt-2 break-all font-mono text-[10px] text-dim">
                          {resource.externalIdentity}
                        </p>
                      </div>
                      <span className="text-[10px] text-dim">{relativeTime(resource.updatedAt)}</span>
                    </div>
                    <div className="mt-4 grid gap-2 xl:grid-cols-2">
                      {resource.views.map((view) => {
                        const branch =
                          typeof view.properties.branch === "string"
                            ? view.properties.branch
                            : "detached"
                        const dirty = view.properties.dirty === true
                        return (
                          <div
                            key={view.id}
                            className={cn(
                              "rounded-[11px] border border-line bg-panel px-4 py-3",
                              view.id === activeView && "border-signal/35 bg-signal-soft/40",
                            )}
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="font-mono text-[11px] font-semibold text-ink">
                                {branch}
                              </span>
                              {dirty && <Badge variant="warning">Dirty</Badge>}
                              {view.id === activeView && <Badge variant="success">Active view</Badge>}
                              {view.revision && (
                                <span className="font-mono text-[10px] text-dim">
                                  {view.revision.slice(0, 10)}
                                </span>
                              )}
                            </div>
                            <p className="mt-2 break-all font-mono text-[10px] leading-5 text-muted">
                              {view.locator}
                            </p>
                          </div>
                        )
                      })}
                      {!resource.views.length && (
                        <div className="rounded-[11px] border border-dashed border-line bg-panel px-4 py-3 text-[11px] leading-5 text-muted">
                          Remote Resource registered. Attach a local checkout with the same origin URL when structural or file context is needed.
                        </div>
                      )}
                    </div>
                  </article>
                ))}
                {!project.resources.length && (
                  <EmptyState
                    icon={<GitFork />}
                    title="No resources attached"
                    detail="The project namespace already exists. Attach material only when it should inform this work context."
                  />
                )}
              </div>
              <GitResourceForm project={project} onRefresh={onRefresh} />
            </CardContent>
          </Card>
        )
      })}

      {!projects.length && (
        <Card>
          <EmptyState
            icon={<FolderKanban />}
            title="No project contexts"
            detail="Create one to give intent, knowledge, and changing resources a shared boundary."
          />
        </Card>
      )}
    </div>
  )
}

function EmptyState({ icon, title, detail }: { icon: ReactNode; title: string; detail: string }) {
  return (
    <div className="flex flex-col items-center px-5 py-12 text-center text-dim">
      <div className="mb-3 rounded-full border border-line bg-panel-raised p-3 [&_svg]:size-5">{icon}</div>
      <p className="text-xs font-semibold text-ink">{title}</p>
      <p className="mt-1.5 max-w-xs text-[11px] leading-5 text-muted">{detail}</p>
    </div>
  )
}

export function Dashboard() {
  const [page, setPage] = useState<Page>("overview")
  const [view, setView] = useState(emptyView)
  const [projects, setProjects] = useState<ProjectNamespace[]>([])
  const [recall, setRecall] = useState(emptyRecall)
  const [requests, setRequests] = useState<ContextRequest[]>([])
  const [needsReviews, setNeedsReviews] = useState<NeedsReview[]>([])
  const [globalMemoryRequests, setGlobalMemoryRequests] = useState<GlobalMemoryRequest[]>([])
  const [memoryReport, setMemoryReport] = useState<MemoryReportDay[]>([])
  const [contextDecisions, setContextDecisions] = useState<ContextDecision[]>([])
  const [modelStatus, setModelStatus] = useState<ModelStatus>(emptyModelStatus)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const refresh = useCallback(async () => {
    try {
      const [
        nextView,
        nextProjects,
        nextRecall,
        nextRequests,
        nextNeedsReviews,
        nextGlobalMemoryRequests,
        nextMemoryReport,
        nextContextDecisions,
        nextModelStatus,
      ] = await Promise.all([
        getView(),
        getProjects(),
        getRecall(),
        getRequests(),
        getNeedsReviews(),
        getGlobalMemoryRequests(),
        getMemoryReport(),
        getContextDecisions(),
        getModelStatus(),
      ])
      setView(nextView)
      setProjects(nextProjects)
      setRecall(nextRecall)
      setRequests(nextRequests)
      setNeedsReviews(nextNeedsReviews)
      setGlobalMemoryRequests(nextGlobalMemoryRequests)
      setMemoryReport(nextMemoryReport)
      setContextDecisions(nextContextDecisions)
      setModelStatus(nextModelStatus)
      setError("")
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not connect to the context plane")
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 30_000)
    const unsubscribe = subscribeToEvents(() => void refresh())
    return () => {
      window.clearInterval(timer)
      unsubscribe()
    }
  }, [refresh])

  const navigation: Array<{ id: Page; label: string; icon: ReactNode; count?: number }> = [
    { id: "overview", label: "Overview", icon: <LayoutDashboard /> },
    { id: "projects", label: "Projects", icon: <FolderKanban />, count: projects.length },
    { id: "delivery", label: "Delivery", icon: <Route />, count: contextDecisions.length },
    { id: "context", label: "Context", icon: <Database />, count: view.topics.length },
    {
      id: "memory",
      label: "Memory review",
      icon: <ShieldCheck />,
      count:
        needsReviews.filter((item) => item.status === "open").length
        + globalMemoryRequests.filter((item) => item.status === "pending").length,
    },
    { id: "sessions", label: "Sessions", icon: <Users />, count: view.sessions.length },
    {
      id: "requests",
      label: "Requests",
      icon: <AlertCircle />,
      count: requests.filter((item) => item.status === "open").length,
    },
    { id: "graph", label: "Code graph", icon: <Network /> },
  ]
  const meta = pageMeta[page]
  const openRequests = requests.filter((request) => request.status === "open")
  const totalDeliveries = view.sessions.reduce((sum, session) => sum + session.items.length, 0)

  return (
    <div className="min-h-screen text-ink">
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-[17rem] border-r border-line bg-panel/95 px-5 py-6 backdrop-blur-xl lg:flex lg:flex-col">
        <div className="flex items-center gap-3 px-1">
          <div className="relative grid size-10 place-items-center overflow-hidden rounded-[13px] border border-signal/25 bg-signal-soft">
            <span className="text-sm font-bold tracking-[-0.06em] text-signal">P</span>
            <span className="absolute -bottom-2 -right-2 size-5 rotate-45 bg-signal/20" />
          </div>
          <div>
            <div className="font-semibold tracking-[-0.025em] text-ink">Purpory</div>
            <div className="mt-0.5 text-[9px] font-semibold uppercase tracking-[0.22em] text-dim">Context plane</div>
          </div>
        </div>

        <p className="fine-label mb-2 mt-10 px-3">Workspace</p>
        <nav className="space-y-1">
          {navigation.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={page === item.id ? "page" : undefined}
              onClick={() => setPage(item.id)}
              className={cn(
                "group relative flex w-full items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm transition [&_svg]:size-4",
                page === item.id
                  ? "bg-signal-soft text-ink"
                  : "text-muted hover:bg-black/[0.035] hover:text-ink",
              )}
            >
              {page === item.id && <span className="absolute -left-5 h-5 w-0.5 rounded-full bg-signal" />}
              <span className={cn(page === item.id && "text-signal")}>{item.icon}</span>
              <span className="flex-1 text-left">{item.label}</span>
              {item.count !== undefined && (
                <span className={cn(
                  "min-w-5 rounded-md px-1.5 py-0.5 text-center text-[10px] tabular-nums",
                  page === item.id ? "bg-signal-soft text-signal" : "text-dim",
                )}>
                  {item.count}
                </span>
              )}
              <ChevronRight className="opacity-0 transition-opacity group-hover:opacity-50" />
            </button>
          ))}
        </nav>

        <div className="mt-auto rounded-[14px] border border-line bg-panel p-4">
          <div className="flex items-center gap-2 text-[11px] font-semibold text-positive">
            <LockKeyhole className="size-3.5" /> Loopback protected
          </div>
          <p className="mt-2 text-[10px] leading-[1.55] text-muted">
            Mutations stay isolated from read-only dashboard links.
          </p>
          <div className="mt-3 flex items-center gap-2 border-t border-line pt-3 text-[10px] text-dim">
            <span className={cn(
              "size-1.5 rounded-full",
              view.diagnostics.integrity === "ok" ? "bg-positive" : "bg-amber-300",
            )} />
            Schema {view.diagnostics.schemaVersion || "—"}
          </div>
        </div>
      </aside>

      <main className="relative lg:pl-[17rem]">
        <header className="sticky top-0 z-10 border-b border-line bg-canvas/85 px-5 py-4 backdrop-blur-xl sm:px-8">
          <div className="mx-auto flex max-w-[1540px] items-center justify-between gap-5">
            <div className="min-w-0">
              <p className="eyebrow">{meta.eyebrow}</p>
              <div className="mt-2 flex min-w-0 items-baseline gap-4">
                <h1 className="shrink-0 text-xl font-semibold tracking-[-0.035em] text-ink sm:text-2xl">{meta.title}</h1>
                <p className="hidden truncate text-xs text-muted xl:block">{meta.description}</p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span className="hidden items-center gap-2 rounded-full border border-line bg-panel px-3 py-2 text-[11px] text-muted sm:flex">
                <span
                  className={cn(
                    "size-1.5 rounded-full",
                    view.diagnostics.integrity === "ok"
                      ? "bg-positive shadow-[0_0_8px_rgba(127,200,155,.42)]"
                      : "bg-amber-300",
                  )}
                />
                {view.diagnostics.integrity === "ok" ? "Store healthy" : "Connecting"}
              </span>
              <Button variant="secondary" size="icon" onClick={() => void refresh()} aria-label="Refresh">
                <RefreshCw className={cn(loading && "animate-spin")} />
              </Button>
            </div>
          </div>
        </header>

        <div className="border-b border-line bg-canvas/80 px-4 py-2 lg:hidden">
          <div className="flex gap-1 overflow-x-auto">
            {navigation.map((item) => (
              <Button
                key={item.id}
                aria-current={page === item.id ? "page" : undefined}
                variant={page === item.id ? "secondary" : "ghost"}
                size="sm"
                onClick={() => setPage(item.id)}
                className={cn(page === item.id && "border-signal/20 text-signal")}
              >
                {item.label}
              </Button>
            ))}
          </div>
        </div>

        <div className="mx-auto max-w-[1540px] p-5 sm:p-8">
          <p className="mb-6 text-sm leading-6 text-muted xl:hidden">{meta.description}</p>
          {error && (
            <div className="mb-5 flex items-center gap-3 rounded-[13px] border border-red-700/20 bg-red-50 px-4 py-3 text-sm text-red-800">
              <AlertCircle className="size-4" /> {error}
            </div>
          )}

          <div key={page} className="page-enter">
            {page === "overview" && (
              <div className="space-y-5">
                <section className="grid grid-cols-2 gap-3 xl:grid-cols-4">
                  <StatCard
                    icon={<Database />}
                    label="Context graph"
                    value={view.diagnostics.counts.nodes ?? 0}
                    detail={`${view.diagnostics.counts.edges ?? 0} edges · ${view.topics.length} curated`}
                    tone="blue"
                  />
                  <StatCard
                    icon={<Users />}
                    label="Agent sessions"
                    value={view.sessions.length}
                    detail={`${totalDeliveries} exact deliveries`}
                    tone="violet"
                  />
                  <StatCard
                    icon={<AlertCircle />}
                    label="Open gaps"
                    value={openRequests.length}
                    detail="awaiting durable context"
                    tone="amber"
                  />
                  <StatCard
                    icon={<BrainCircuit />}
                    label="Recalled"
                    value={recall.preferred.length}
                    detail="cross-session preferred"
                    tone="teal"
                  />
                </section>
                <section className="grid items-start gap-5 xl:grid-cols-[1.2fr_.8fr]">
                  <SessionList sessions={view.sessions.slice(0, 5)} />
                  <div className="space-y-5">
                    <RecallPanel recall={recall} />
                    <RequestQueue requests={openRequests.slice(0, 3)} topics={view.topics} onRefresh={refresh} />
                  </div>
                </section>
              </div>
            )}
            {page === "projects" && (
              <ProjectPanel
                projects={projects}
                activeProject={view.project}
                activeView={view.graphProject}
                onRefresh={refresh}
              />
            )}
            {page === "delivery" && (
              <PreparationPanel
                project={view.project}
                decisions={contextDecisions}
                modelStatus={modelStatus}
                onRefresh={refresh}
              />
            )}
            {page === "context" && <TopicTable topics={view.topics} onRefresh={refresh} />}
            {page === "memory" && (
              <MemoryGovernancePanel
                globalRequests={globalMemoryRequests}
                reviews={needsReviews}
                report={memoryReport}
                topics={view.topics}
                onRefresh={refresh}
              />
            )}
            {page === "sessions" && <SessionList sessions={view.sessions} />}
            {page === "requests" && (
              <RequestQueue requests={requests} topics={view.topics} onRefresh={refresh} />
            )}
            {page === "graph" && <GraphPanel />}
          </div>
        </div>
      </main>
    </div>
  )
}
