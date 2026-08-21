import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  AssignResource, ConfirmMemory, ContextDecisions, ContextFeedback, ContextRequests, CreateProject, DeleteMemory,
  EmbeddingStatus, Explain, Graph, InstallModel, Memories, ModelState, NeedsReviews,
  Observations, Projects, Query, Reconciliations, Remember, ResolveContextRequest,
  ResolveNeedsReview, SelectModel, SelectProject, StartModels, Status, SyncEmbeddings, UnassignResource,
  Update, Workspace,
} from "../wailsjs/go/main/App";
import type { app, graph, memory, prepare, project, reconcile } from "../wailsjs/go/models";
import { Dropdown } from "./Dropdown";
import { GraphView, NavIcon, NodeDetails, ProjectPicker, ReconciliationQueue, ResourceAssignments, WorkspaceAttention, WorkspaceHistory, WorkspaceTopology, reconciliationLabel, relativeTime, type Page } from "./ProjectViews";

const projectPages: { id: Page; label: string; description: string }[] = [
  { id: "overview", label: "Overview", description: "프로젝트의 현재 상태를 한눈에 봅니다." },
  { id: "workspace", label: "Workspace", description: "Repository, View, Session의 연결을 살펴봅니다." },
  { id: "reconcile", label: "Reconcile", description: "등록된 Reconcile 작업과 진행 상태를 살펴봅니다." },
  { id: "graph", label: "Graph", description: "검색을 중심으로 Intent와 Material·Knowledge의 주변 관계를 탐색합니다." },
];
const globalPages: { id: Page; label: string; description: string }[] = [
  { id: "projects", label: "Projects", description: "Project를 만들고 관찰된 Repository를 연결합니다." },
  { id: "settings", label: "Settings", description: "모든 Project에 적용되는 로컬 모델을 설정합니다." },
];
const pages = [...projectPages, ...globalPages];

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [selectedViewID, setSelectedViewID] = useState("");
  const [status, setStatus] = useState<app.Status>();
  const [projects, setProjects] = useState<project.Project[]>([]);
  const [observations, setObservations] = useState<project.Observation[]>([]);
  const [modelState, setModelState] = useState<app.ModelState>();
  const [embeddingStatus, setEmbeddingStatus] = useState<app.EmbeddingStatus>();
  const [memories, setMemories] = useState<memory.Memory[]>([]);
  const [requests, setRequests] = useState<prepare.ContextRequest[]>([]);
  const [reviews, setReviews] = useState<memory.Review[]>([]);
  const [decisions, setDecisions] = useState<prepare.Decision[]>([]);
  const [workspace, setWorkspace] = useState<project.Workspace>();
  const [reconciliations, setReconciliations] = useState<reconcile.Run[]>([]);
  const [results, setResults] = useState<app.QueryResult>();
  const [materialGraph, setMaterialGraph] = useState<app.GraphResult>();
  const [selectedNode, setSelectedNode] = useState<graph.Node>();
  const [explanation, setExplanation] = useState<app.ExplainResult>();
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [workspaceMode, setWorkspaceMode] = useState<"current" | "history">("current");
  const [query, setQuery] = useState("");
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState("note");
  const refreshGeneration = useRef(0);

  const refresh = useCallback(async () => {
    const generation = ++refreshGeneration.current;
    const [nextStatus, nextProjects, nextObservations, nextMemories, nextModel, nextEmbedding, nextWorkspace, nextReconciliations, nextGraph, nextRequests, nextReviews, nextDecisions] = await Promise.all([
      Status(), Projects(), Observations(), Memories(""), ModelState(), EmbeddingStatus(), Workspace(), Reconciliations(), Graph("", 80),
      ContextRequests(""), NeedsReviews(""), ContextDecisions(30),
    ]);
    if (generation !== refreshGeneration.current) return;
    setStatus(nextStatus);
    setProjects(nextProjects ?? []);
    setObservations(nextObservations ?? []);
    setMemories(nextMemories ?? []);
    setModelState(nextModel);
    setEmbeddingStatus(nextEmbedding);
    setWorkspace(nextWorkspace);
    setReconciliations(nextReconciliations ?? []);
    setMaterialGraph(nextGraph);
    setRequests(nextRequests ?? []);
    setReviews(nextReviews ?? []);
    setDecisions(nextDecisions ?? []);
  }, []);

  useEffect(() => {
    const refreshView = () => void refresh().catch(error => setMessage(errorMessage(error)));
    refreshView();
    window.addEventListener("focus", refreshView);
    return () => window.removeEventListener("focus", refreshView);
  }, [refresh]);

  useEffect(() => {
    if (status && !status.project.id && !globalPages.some(item => item.id === page)) setPage("projects");
  }, [page, status]);

  useEffect(() => {
    if (!message) return;
    const timer = window.setTimeout(() => setMessage(""), 4000);
    return () => window.clearTimeout(timer);
  }, [message]);

  const reconciliationActive = reconciliations.some(run => run.phase !== "completed" && run.phase !== "failed");
  useEffect(() => {
    if (!reconciliationActive) return;
    const timer = window.setInterval(() => void refresh().catch(error => setMessage(errorMessage(error))), 1500);
    return () => window.clearInterval(timer);
  }, [reconciliationActive, refresh]);

  async function updateProject() {
    await perform(async () => {
      const result = await Update();
      setMessage(`${result.materialCount}개 Material · ${result.extracted}개 추출 · ${result.entityCount}개 지식 · ${result.relationCount}개 관계`);
      await refresh();
    });
  }

  async function switchProject(projectID: string) {
    if (!projectID || projectID === status?.project.id) return;
    refreshGeneration.current++;
    await perform(async () => {
      setStatus(await SelectProject(projectID));
      clearProjectView();
      await refresh();
      setMessage(`${projects.find(item => item.id === projectID)?.name ?? projectID} 프로젝트로 전환했습니다.`);
    });
  }

  async function assignResource(projectID: string, resourceID: string) {
    await perform(async () => {
      await AssignResource(projectID, resourceID);
      const target = projects.find(item => item.id === projectID)?.name ?? projectID;
      setMessage(`${target} Project에 Repository를 추가했습니다.`);
      await refresh();
    });
  }

  async function createProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = String(new FormData(form).get("name") ?? "").trim();
    if (!name) return;
    await perform(async () => {
      setStatus(await CreateProject(name));
      form.reset();
      clearProjectView();
      await refresh();
      setMessage(`${name} Project를 만들었습니다. 이제 Repository를 연결하세요.`);
    });
  }

  async function unassignResource(projectID: string, resourceID: string) {
    await perform(async () => {
      if (!await UnassignResource(projectID, resourceID)) throw new Error("연결된 Repository를 찾을 수 없습니다.");
      setMessage("Project에서 Repository 연결을 해제했습니다.");
      await refresh();
    });
  }

  function clearProjectView() {
    setSelectedViewID("");
    setSelectedNode(undefined);
    setResults(undefined);
    setQuery("");
    setExplanation(undefined);
    setKey("");
    setValue("");
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    await perform(async () => {
      const found = await Query(query, 12);
      setResults(found);
      const first = found.nodes?.[0];
      if (first) {
        setSelectedNode(first);
        setExplanation(await Explain(first.id));
      } else {
        setSelectedNode(undefined);
        setExplanation(undefined);
      }
      setMessage(`${found.nodes?.length ?? 0}개 노드`);
    });
  }

  function clearGraphSearch() {
    setQuery("");
    setResults(undefined);
    setSelectedNode(undefined);
    setExplanation(undefined);
  }

  async function explainNode(node: graph.Node) {
    setSelectedNode(node);
    setExplanation(undefined);
    await perform(async () => setExplanation(await Explain(node.id)));
  }

  async function remember(event: FormEvent) {
    event.preventDefault();
    if (!key.trim() || !value.trim()) return;
    await perform(async () => {
      const result = await Remember(key, kind, value, null);
      setKey("");
      setValue("");
      setSelectedNode(undefined);
      setExplanation(undefined);
      setMessage(`메모리 ${result.action}`);
      await refresh();
    });
  }

  async function resolveRequest(event: FormEvent<HTMLFormElement>, requestID: number) {
    event.preventDefault();
    const key = String(new FormData(event.currentTarget).get("memory") ?? "").trim();
    if (!key) return;
    await perform(async () => {
      if (!await ResolveContextRequest(requestID, key)) throw new Error("이미 해결됐거나 찾을 수 없는 요청입니다.");
      setMessage(`요청 #${requestID}을 ${key}로 해결했습니다.`);
      await refresh();
    });
  }

  async function resolveReview(reviewID: number) {
    await perform(async () => {
      if (!await ResolveNeedsReview(reviewID, "keep", null)) throw new Error("이미 해결됐거나 찾을 수 없는 리뷰입니다.");
      setMessage(`리뷰 #${reviewID}에서 현재 메모리를 유지했습니다.`);
      await refresh();
    });
  }

  async function changeReview(event: FormEvent<HTMLFormElement>, review: memory.Review) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const kind = String(data.get("kind") ?? "note");
    const value = String(data.get("value") ?? "").trim();
    if (!value) return;
    await perform(async () => {
      const saved = await Remember(review.key, kind, value, null);
      if (!await ResolveNeedsReview(review.id, "change", saved.versionId)) throw new Error("리뷰를 해결하지 못했습니다.");
      setMessage(`${review.key}를 수정하고 리뷰 #${review.id}을 해결했습니다.`);
      await refresh();
    });
  }

  async function submitFeedback(event: FormEvent<HTMLFormElement>, decisionID: number) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const verdict = String(data.get("verdict") ?? "correct");
    const expectedAction = String(data.get("expectedAction") ?? "").trim();
    if (verdict === "incorrect" && !expectedAction) {
      setMessage("잘못된 결정에는 기대한 동작을 선택해야 합니다.");
      return;
    }
    const note = String(data.get("note") ?? "").trim();
    const expectedKeys = String(data.get("expectedKeys") ?? "").split(",").map(item => item.trim()).filter(Boolean);
    await perform(async () => {
      await ContextFeedback({ decisionId: decisionID, verdict, expectedAction: expectedAction || undefined, expectedKeys, note: note || undefined });
      setMessage(`결정 #${decisionID} 피드백을 저장했습니다.`);
      await refresh();
    });
  }

  async function confirmMemory(key: string) {
    await perform(async () => {
      if (!await ConfirmMemory(key)) throw new Error(`메모리 ${key}를 찾을 수 없습니다.`);
      setMessage(`${key}가 여전히 유효함을 확인했습니다.`);
      await refresh();
    });
  }

  async function deleteMemory(key: string) {
    if (!window.confirm(`${key} 메모리를 삭제할까요? 버전 기록은 유지됩니다.`)) return;
    await perform(async () => {
      if (!await DeleteMemory(key)) throw new Error(`메모리 ${key}를 찾을 수 없습니다.`);
      setSelectedNode(undefined);
      setExplanation(undefined);
      setMessage(`${key}를 삭제했습니다.`);
      await refresh();
    });
  }

  function editMemory(item: memory.Memory) {
    setKind(item.kind);
    setKey(item.key);
    setValue(item.value ?? item.source ?? "");
    window.requestAnimationFrame(() => document.getElementById("memory-value")?.focus());
  }

  function openMemory(key: string) {
    setPage("graph");
    setQuery(key);
    void perform(async () => {
      const found = await Query(key, 12);
      setResults(found);
      const match = found.nodes?.find(item => item.ref === key && item.owner === "durable");
      if (match) {
        setSelectedNode(match);
        setExplanation(await Explain(match.id));
      }
    });
  }

  async function startModels() {
    await perform(async () => {
      const result = await StartModels(10);
      if (!result.available) throw new Error(result.error || "Ollama를 시작하지 못했습니다.");
      setMessage(`Ollama ${result.version || "runtime"}이 준비됐습니다.`);
      await refresh();
    });
  }

  async function selectModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const role = String(data.get("role") ?? "");
    const name = String(data.get("model") ?? "").trim();
    if (!name) return;
    await perform(async () => {
      await SelectModel(role, name);
      setMessage(`${role} 모델을 ${name}(으)로 선택했습니다. 모든 Project에 적용됩니다.`);
      await refresh();
    });
  }

  async function installModel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const role = String(data.get("installRole") ?? "");
    const name = String(data.get("installModel") ?? "").trim();
    if (!name) return;
    await perform(async () => {
      await InstallModel(name, role);
      setMessage(role ? `${name} 설치 및 전역 ${role} 선택을 완료했습니다.` : `${name} 설치를 완료했습니다.`);
      await refresh();
    });
  }

  async function syncEmbeddings() {
    await perform(async () => {
      const result = await SyncEmbeddings(0);
      setMessage(`${result.embedded}개 embedding 생성 · ${result.current}개 최신`);
      await refresh();
    });
  }

  async function perform(operation: () => Promise<void>) {
    setBusy(true);
    try {
      await operation();
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const views = workspace?.resources.flatMap(resource => resource.views ?? []) ?? [];
  const sessions = views.flatMap(view => view.sessions ?? []);
  const allSessions = [...sessions, ...(workspace?.unmappedSessions ?? [])];
  const model = modelState?.ollama;
  const currentPage = pages.find(item => item.id === page)!;
  const globalPage = globalPages.some(item => item.id === page);
  const recentSessions = [...allSessions].sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt)).slice(0, 5);
  const activeReconciliations = reconciliations.filter(run => run.phase !== "completed" && run.phase !== "failed");
  const openRequests = requests.filter(item => item.status === "open");
  const resolvedRequests = requests.filter(item => item.status === "resolved");
  const openReviews = reviews.filter(item => item.status === "open");
  const resolvedReviews = reviews.filter(item => item.status === "resolved");
  const selectedMemory = explanation?.memory ?? (selectedNode?.owner === "durable" ? memories.find(item => item.key === selectedNode.ref) : undefined);
  const graphNodes = results?.nodes ?? materialGraph?.nodes ?? [];
  const graphEdges = results?.edges ?? materialGraph?.edges ?? [];
  const latestReconciliation = reconciliations[0];
  const reconciliationBySession = new Map<string, reconcile.Run>();
  for (const run of reconciliations) if (!reconciliationBySession.has(run.sessionId)) reconciliationBySession.set(run.sessionId, run);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span aria-hidden="true">P</span><div>Purpory<small>PROJECT MEMORY</small></div></div>
        <nav aria-label="주요 메뉴">
          <span className="navScope">CURRENT PROJECT</span>
          {projectPages.map(item => <button type="button" key={item.id} disabled={!status?.project.id} aria-current={page === item.id ? "page" : undefined} onClick={() => setPage(item.id)}>
            <NavIcon page={item.id} /><span>{item.label}</span>{item.id === "workspace" && openRequests.length + openReviews.length > 0 && <b>{openRequests.length + openReviews.length}</b>}{item.id === "reconcile" && activeReconciliations.length > 0 && <b>{activeReconciliations.length}</b>}
          </button>)}
          <span className="navScope global">GLOBAL</span>
          {globalPages.map(item => <button type="button" key={item.id} aria-current={page === item.id ? "page" : undefined} onClick={() => setPage(item.id)}><NavIcon page={item.id} /><span>{item.label}</span>{item.id === "projects" && <b>{projects.length}</b>}</button>)}
        </nav>
        <div className="engine">
          <span className={model?.available ? "dot online" : "dot"} />
          <div><strong>{model?.available ? "Ollama 연결됨" : "Ollama 선택 사항"}</strong><small>{model?.version ?? "로컬 모델 없이도 동작"}</small></div>
        </div>
      </aside>

      <main className="appMain">
        <header className="topbar">
          <div><p className="eyebrow">{currentPage.label}</p><h1>{currentPage.label}</h1><p>{currentPage.description}</p></div>
          <div className="projectControl">{globalPage ? <span className="globalScope">GLOBAL · 모든 Project</span> : <><ProjectPicker current={status?.project} projects={projects} busy={busy} onSelect={projectID => void switchProject(projectID)} onManage={() => setPage("projects")} /><button className="secondary" disabled={busy || !status?.project.id} onClick={() => void updateProject()}>{busy ? "처리 중…" : "↻ 업데이트"}</button></>}</div>
        </header>

        <div className="pageBody">
        {message && <p className="notice" aria-live="polite"><span className="dot online" />{message}</p>}

        {page === "overview" && <><section className="metrics" aria-label="프로젝트 요약">
          <article><span>MEMORIES</span><strong>{memories.length}</strong><p>프로젝트에 저장된 결정과 지식</p></article>
          <article><span>REPOSITORIES</span><strong>{workspace?.resources.length ?? 0}</strong><p>Project에 연결된 Repository</p></article>
          <article><span>VIEWS</span><strong>{views.length}</strong><p>발견된 worktree와 작업 폴더</p></article>
          <article><span>SESSIONS</span><strong>{allSessions.filter(session => session.status === "active").length}</strong><p>{allSessions.length}개 기록 · 현재 맥락을 사용하는 에이전트</p></article>
          <article><span>ATTENTION</span><strong>{openRequests.length + openReviews.length}</strong><p>{openRequests.length}개 요청 · {openReviews.length}개 메모리 리뷰</p></article>
          <article><span>RECONCILE</span><strong>{activeReconciliations.length || "—"}</strong><p>{activeReconciliations[0] ? reconciliationLabel(activeReconciliations[0].phase) : latestReconciliation ? `최근 ${reconciliationLabel(latestReconciliation.phase)}` : "실행 기록 없음"}</p></article>
        </section>
        <section className="overviewGrid">
          <article className="panel attentionCard"><div className="sectionTitle"><div><p className="eyebrow">ATTENTION</p><h2>확인이 필요한 항목</h2></div><button className="textButton" onClick={() => { setWorkspaceMode("current"); setPage("workspace"); }}>Workspace 열기 →</button></div>
            <div className="attentionRows"><button onClick={() => { setWorkspaceMode("current"); setPage("workspace"); }}><span>Context 요청</span><strong>{openRequests.length}</strong><small>답을 기다리는 요청</small></button><button onClick={() => { setWorkspaceMode("current"); setPage("workspace"); }}><span>메모리 리뷰</span><strong>{openReviews.length}</strong><small>유효성 확인 필요</small></button></div>
          </article>
          <article className="panel recentCard"><div className="sectionTitle"><div><p className="eyebrow">RECENT SESSIONS</p><h2>최근 작업</h2></div><button className="textButton" onClick={() => setPage("workspace")}>Workspace 열기 →</button></div>
            {recentSessions.length === 0 ? <p className="empty">아직 기록된 Session이 없습니다.</p> : <div className="recentList">{recentSessions.map(session => { const run = reconciliationBySession.get(session.id); return <button key={session.id} onClick={() => setPage("workspace")}><span className={`sessionStatus ${session.status}`} /><div><strong>{session.agent}</strong><small>{session.id}{run ? ` · ${reconciliationLabel(run.phase)}` : ""}</small></div><time>{relativeTime(session.updatedAt)}</time></button>; })}</div>}
          </article>
        </section></>}

        {page === "workspace" && workspace && <section className="workspacePage">
          <div className="workspaceTabs" role="tablist" aria-label="Workspace 보기"><button type="button" role="tab" aria-selected={workspaceMode === "current"} onClick={() => { setWorkspaceMode("current"); setSelectedViewID(""); }}>Current <b>{openRequests.length + openReviews.length}</b></button><button type="button" role="tab" aria-selected={workspaceMode === "history"} onClick={() => { setWorkspaceMode("history"); setSelectedViewID(""); }}>History</button></div>
          {workspaceMode === "current" ? <>
            <WorkspaceTopology workspace={workspace} selectedViewID={selectedViewID} onSelectView={setSelectedViewID} />
            <WorkspaceAttention requests={openRequests} reviews={openReviews} memories={memories} busy={busy} onResolveRequest={(event, id) => void resolveRequest(event, id)} onResolveReview={id => void resolveReview(id)} onChangeReview={(event, review) => void changeReview(event, review)} onOpenMemory={openMemory} />
          </> : <>
            <WorkspaceTopology history workspace={workspace} selectedViewID={selectedViewID} onSelectView={setSelectedViewID} />
            <WorkspaceHistory requests={resolvedRequests} reviews={resolvedReviews} decisions={decisions} busy={busy} onFeedback={(event, id) => void submitFeedback(event, id)} />
          </>}
        </section>}

        {page === "reconcile" && <ReconciliationQueue runs={reconciliations} />}

        {page === "graph" && <section className="panel graphPage">
          <div className="sectionTitle"><div><p className="eyebrow">PROJECT GRAPH</p><h2>검색 중심 관계 탐색</h2></div><span>{materialGraph?.totalNodes ?? 0} nodes · {materialGraph?.totalEdges ?? 0} edges</span></div>
          <form className="search graphSearch" onSubmit={event => void search(event)}>
            <label htmlFor="query">질문, 지식 또는 Material</label>
            <div><input id="query" type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="예: 프로젝트 목표 또는 데이터베이스 결정" /><button disabled={busy}>주변 그래프 찾기</button>{results && <button type="button" className="secondary" onClick={clearGraphSearch}>전체 보기</button>}</div>
          </form>
          {results && (results.matches?.length ?? 0) > 0 && <div className="graphMatches" aria-label="검색된 시작 노드">{results.matches.slice(0, 8).map(match => <button type="button" className={match.node.id === selectedNode?.id ? "selected" : ""} key={match.node.id} onClick={() => void explainNode(match.node)}><span>{match.signals.map(signal => signal.kind).join(" + ")}</span><strong>{match.node.label}</strong></button>)}</div>}
          <div className="contextGraph graphExplorer">
            <GraphView nodes={graphNodes} edges={graphEdges} matches={results?.matches} searchQuery={results ? query : undefined} selectedID={selectedNode?.id} emptyMessage={results ? "검색과 연결된 노드를 찾지 못했습니다." : undefined} onSelect={node => void explainNode(node)} />
            <NodeDetails node={selectedNode} explanation={explanation} durable={selectedMemory} busy={busy} onSelect={node => void explainNode(node)} onEdit={editMemory} onConfirm={key => void confirmMemory(key)} onDelete={key => void deleteMemory(key)} />
          </div>
          {key && <section className="memoryEditor">
            <div><strong>지속 메모리 편집</strong><button type="button" className="textButton" onClick={() => { setKey(""); setValue(""); }}>닫기</button></div>
            <form className="memoryForm" onSubmit={event => void remember(event)}>
              <label htmlFor="memory-kind">종류</label><Dropdown id="memory-kind" value={kind} onChange={setKind} options={[{ value: "note", label: "지식" }, { value: "decision", label: "결정" }, { value: "reference", label: "참조" }]} />
              <label htmlFor="memory-key">키</label><input id="memory-key" value={key} readOnly />
              <label htmlFor="memory-value">내용</label><textarea id="memory-value" value={value} onChange={event => setValue(event.target.value)} placeholder="왜 이 결정을 내렸는지 함께 기록하세요." />
              <button disabled={busy}>저장</button>
            </form>
          </section>}
        </section>}

        {page === "projects" && <ResourceAssignments observations={observations} projects={projects} currentID={status?.project.id} busy={busy} onCreate={event => void createProject(event)} onSelect={projectID => { setPage("overview"); void switchProject(projectID); }} onAssign={(projectID, resourceID) => void assignResource(projectID, resourceID)} onUnassign={(projectID, resourceID) => void unassignResource(projectID, resourceID)} />}

        {page === "settings" && <section className="settingsGrid">
          <section className="panel modelCard">
            <div className="cardTitle"><div><p className="eyebrow">GLOBAL SETTINGS</p><h2>Ollama와 Embedding</h2></div><span className="status globalStatus">GLOBAL</span></div>
            <p className="settingsIntro">선택한 모델은 모든 Project에 적용됩니다. 구조 분석과 기본 검색은 모델 없이도 동작합니다.</p>
            <div className="modelSummary"><p><strong>{model?.version || "Ollama 미연결"}</strong><span>현재 Project Embedding {embeddingStatus?.current ?? 0}개 최신 · {embeddingStatus?.pending ?? 0}개 대기</span></p><button className="secondary" disabled={busy} onClick={() => void startModels()}>Ollama 시작</button><button disabled={busy || !status?.project.id || !model?.available || (embeddingStatus?.pending ?? 0) === 0} onClick={() => void syncEmbeddings()}>현재 Project 동기화</button></div>
            <div className="modelRoles">{(modelState?.selected ?? []).map(selected => <form key={`${selected.role}-${selected.model}`} onSubmit={event => void selectModel(event)}>
              <input type="hidden" name="role" value={selected.role} /><label htmlFor={`model-${selected.role}`}>{selected.role} <small>global · {selected.source}</small></label><div><input id={`model-${selected.role}`} name="model" defaultValue={selected.model || ""} placeholder="모델 태그" /><button className="secondary" disabled={busy}>선택</button></div>
            </form>)}</div>
            <form className="installForm" onSubmit={event => void installModel(event)}><label htmlFor="install-model">모델 설치</label><div><input id="install-model" name="installModel" placeholder="예: qwen3:4b" required /><Dropdown name="installRole" ariaLabel="설치 후 사용할 역할" options={[{ value: "", label: "설치만" }, { value: "gate", label: "gate" }, { value: "reconcile", label: "reconcile" }, { value: "embedding", label: "embedding" }]} /><button disabled={busy}>설치</button></div></form>
          </section>
          <aside className="panel settingsNote"><p className="eyebrow">GLOBAL SCOPE</p><h2>모든 Project에 적용</h2><p>모델 설치와 역할 선택은 전역입니다. Memory, Graph, Workspace, Reconcile 같은 작업 데이터는 선택한 Project에만 저장됩니다.</p></aside>
        </section>}
        </div>
      </main>
    </div>
  );
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
