import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  ConfirmMemory, ContextDecisions, ContextFeedback, ContextRequests, DeleteMemory,
  EmbeddingStatus, Explain, Graph, InstallModel, Memories, ModelState, NeedsReviews,
  Path, Prepare, Projects, Query, Reconciliations, Remember, ResolveContextRequest,
  ResolveNeedsReview, SelectModel, SelectProject, StartModels, Status, SyncEmbeddings,
  Update, Workspace,
} from "../wailsjs/go/main/App";
import type { app, graph, memory, prepare, project, reconcile } from "../wailsjs/go/models";
import { GraphView, NavIcon, NodeDetails, ProjectPicker, WorkspaceTopology, reconciliationLabel, relativeTime, type Page } from "./ProjectViews";

const pages: { id: Page; label: string; description: string }[] = [
  { id: "overview", label: "Overview", description: "프로젝트의 현재 상태를 한눈에 봅니다." },
  { id: "workspace", label: "Workspace", description: "Resource, View, Session의 연결을 살펴봅니다." },
  { id: "search", label: "Search", description: "프로젝트 안의 지식과 맥락을 찾습니다." },
  { id: "graph", label: "Graph", description: "Intent와 실제 Material·Knowledge의 연결을 탐색합니다." },
  { id: "inbox", label: "Inbox", description: "확인이 필요한 요청과 판단을 처리합니다." },
  { id: "memory", label: "Memory", description: "오래 유지할 프로젝트 맥락을 관리합니다." },
  { id: "settings", label: "Settings", description: "로컬 모델과 Embedding을 설정합니다." },
];

export default function App() {
  const [page, setPage] = useState<Page>("overview");
  const [selectedViewID, setSelectedViewID] = useState("");
  const [status, setStatus] = useState<app.Status>();
  const [projects, setProjects] = useState<project.Project[]>([]);
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
  const [pathResult, setPathResult] = useState<graph.Path>();
  const [prepared, setPrepared] = useState<prepare.Result>();
  const [message, setMessage] = useState("준비됨");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [graphScope, setGraphScope] = useState("");
  const [pathSource, setPathSource] = useState("");
  const [pathTarget, setPathTarget] = useState("");
  const [prepareMessage, setPrepareMessage] = useState("");
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [kind, setKind] = useState("note");

  const refresh = useCallback(async () => {
    const [nextStatus, nextProjects, nextMemories, nextModel, nextEmbedding, nextWorkspace, nextReconciliations, nextGraph, nextRequests, nextReviews, nextDecisions] = await Promise.all([
      Status(), Projects(), Memories(""), ModelState(), EmbeddingStatus(), Workspace(), Reconciliations(), Graph("", 80),
      ContextRequests("open"), NeedsReviews("open"), ContextDecisions(30),
    ]);
    setStatus(nextStatus);
    setProjects(nextProjects ?? []);
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
    await perform(async () => {
      await SelectProject(projectID);
      setSelectedViewID("");
      setSelectedNode(undefined);
      setResults(undefined);
      setExplanation(undefined);
      setPathResult(undefined);
      setPrepared(undefined);
      await refresh();
      setMessage(`${projects.find(item => item.id === projectID)?.name ?? projectID} 프로젝트로 전환했습니다.`);
    });
  }

  async function search(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    await perform(async () => {
      const found = await Query(query, 20);
      setResults(found);
      const first = found.nodes?.[0];
      if (first) {
        setSelectedNode(first);
        setExplanation(await Explain(first.id));
      }
      setMessage(`${found.memories?.length ?? 0}개 메모리 · ${found.nodes?.length ?? 0}개 노드`);
    });
  }

  async function loadGraph(event: FormEvent) {
    event.preventDefault();
    await perform(async () => {
      const found = await Graph(graphScope, 80);
      setMaterialGraph(found);
      setMessage(`${found.totalNodes}개 노드 · ${found.totalEdges}개 관계`);
    });
  }

  async function explainNode(node: graph.Node) {
    setSelectedNode(node);
    setExplanation(undefined);
    await perform(async () => setExplanation(await Explain(node.id)));
  }

  async function findPath(event: FormEvent) {
    event.preventDefault();
    if (!pathSource.trim() || !pathTarget.trim()) return;
    await perform(async () => {
      const found = await Path(pathSource, pathTarget);
      setPathResult(found);
      setMessage(`${found.nodes?.length ?? 0}개 노드를 잇는 경로`);
    });
  }

  async function prepareContext(event: FormEvent) {
    event.preventDefault();
    if (!prepareMessage.trim()) return;
    await perform(async () => {
      const result = await Prepare(prepareMessage, 2000);
      setPrepared(result);
      setMessage(result.action === "retrieve" ? "전달할 Context를 준비했습니다." : result.action === "ask" ? "사용자 확인이 필요합니다." : "전달할 Context가 없습니다.");
    });
  }

  async function remember(event: FormEvent) {
    event.preventDefault();
    if (!key.trim() || !value.trim()) return;
    await perform(async () => {
      const result = await Remember(key, kind, value, null);
      setKey("");
      setValue("");
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
      setMessage(`${key}를 삭제했습니다.`);
      await refresh();
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
      setMessage(`${role} 모델을 ${name}(으)로 선택했습니다.`);
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
      setMessage(`${name} 설치를 완료했습니다.`);
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
  const recentSessions = [...allSessions].sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt)).slice(0, 5);
  const activeReconciliations = reconciliations.filter(run => run.phase !== "completed" && run.phase !== "failed");
  const latestReconciliation = reconciliations[0];
  const reconciliationBySession = new Map<string, reconcile.Run>();
  for (const run of reconciliations) if (!reconciliationBySession.has(run.sessionId)) reconciliationBySession.set(run.sessionId, run);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand"><span aria-hidden="true">P</span><div>Purpory<small>PROJECT MEMORY</small></div></div>
        <nav aria-label="주요 메뉴">
          {pages.map(item => <button type="button" key={item.id} aria-current={page === item.id ? "page" : undefined} onClick={() => setPage(item.id)}>
            <NavIcon page={item.id} /><span>{item.label}</span>{item.id === "inbox" && requests.length + reviews.length > 0 && <b>{requests.length + reviews.length}</b>}
          </button>)}
        </nav>
        <div className="engine">
          <span className={model?.available ? "dot online" : "dot"} />
          <div><strong>{model?.available ? "Ollama 연결됨" : "Ollama 선택 사항"}</strong><small>{model?.version ?? "로컬 모델 없이도 동작"}</small></div>
        </div>
      </aside>

      <main className="appMain">
        <header className="topbar">
          <div><p className="eyebrow">{currentPage.label}</p><h1>{currentPage.label}</h1><p>{currentPage.description}</p></div>
          <div className="projectControl"><ProjectPicker current={status?.project} projects={projects} busy={busy} onSelect={projectID => void switchProject(projectID)} /><button className="secondary" disabled={busy} onClick={() => void updateProject()}>{busy ? "처리 중…" : "↻ 업데이트"}</button></div>
        </header>

        <div className="pageBody">
        <p className="notice" aria-live="polite"><span className="dot online" />{message}</p>

        {page === "overview" && <><section className="metrics" aria-label="프로젝트 요약">
          <article><span>MEMORIES</span><strong>{memories.length}</strong><p>프로젝트에 저장된 결정과 지식</p></article>
          <article><span>RESOURCES</span><strong>{workspace?.resources.length ?? 0}</strong><p>프로젝트에 연결된 저장소</p></article>
          <article><span>VIEWS</span><strong>{views.length}</strong><p>발견된 worktree와 작업 폴더</p></article>
          <article><span>SESSIONS</span><strong>{allSessions.filter(session => session.status === "active").length}</strong><p>{allSessions.length}개 기록 · 현재 맥락을 사용하는 에이전트</p></article>
          <article><span>ATTENTION</span><strong>{requests.length + reviews.length}</strong><p>{requests.length}개 요청 · {reviews.length}개 메모리 리뷰</p></article>
          <article><span>RECONCILE</span><strong>{activeReconciliations.length || "—"}</strong><p>{activeReconciliations[0] ? reconciliationLabel(activeReconciliations[0].phase) : latestReconciliation ? `최근 ${reconciliationLabel(latestReconciliation.phase)}` : "실행 기록 없음"}</p></article>
        </section>
        <section className="overviewGrid">
          <article className="panel attentionCard"><div className="sectionTitle"><div><p className="eyebrow">ATTENTION</p><h2>확인이 필요한 항목</h2></div><button className="textButton" onClick={() => setPage("inbox")}>Inbox 열기 →</button></div>
            <div className="attentionRows"><button onClick={() => setPage("inbox")}><span>Context 요청</span><strong>{requests.length}</strong><small>답을 기다리는 요청</small></button><button onClick={() => setPage("inbox")}><span>메모리 리뷰</span><strong>{reviews.length}</strong><small>유효성 확인 필요</small></button></div>
          </article>
          <article className="panel recentCard"><div className="sectionTitle"><div><p className="eyebrow">RECENT SESSIONS</p><h2>최근 작업</h2></div><button className="textButton" onClick={() => setPage("workspace")}>Workspace 열기 →</button></div>
            {recentSessions.length === 0 ? <p className="empty">아직 기록된 Session이 없습니다.</p> : <div className="recentList">{recentSessions.map(session => { const run = reconciliationBySession.get(session.id); return <button key={session.id} onClick={() => setPage("workspace")}><span className={`sessionStatus ${session.status}`} /><div><strong>{session.agent}</strong><small>{session.id}{run ? ` · ${reconciliationLabel(run.phase)}` : ""}</small></div><time>{relativeTime(session.updatedAt)}</time></button>; })}</div>}
          </article>
        </section></>}

        {page === "workspace" && workspace && <WorkspaceTopology workspace={workspace} reconciliations={reconciliations} selectedViewID={selectedViewID} onSelectView={setSelectedViewID} />}

        {page === "search" && <section className="panel">
          <div className="sectionTitle"><div><p className="eyebrow">RETRIEVE</p><h2>필요한 맥락 찾기</h2></div><span>프로젝트 범위</span></div>
          <form className="search" onSubmit={event => void search(event)}>
            <label htmlFor="query">질문 또는 Material 이름</label>
            <div><input id="query" value={query} onChange={event => setQuery(event.target.value)} placeholder="예: 프로젝트 목표 또는 데이터베이스 결정" /><button disabled={busy}>검색</button></div>
          </form>
          {results && (results.nodes?.length ?? 0) > 0 && <div className="contextGraph">
            <GraphView nodes={results.nodes ?? []} edges={results.edges ?? []} selectedID={selectedNode?.id} onSelect={node => void explainNode(node)} />
            <NodeDetails node={selectedNode} explanation={explanation} onSelect={node => void explainNode(node)} />
          </div>}
          {results && <div className="results">
            {(results.memories ?? []).map(item => <article key={item.key}><span>{item.kind}</span><strong>{item.key}</strong><p>{item.value ?? item.source}</p></article>)}
            {(results.nodes ?? []).slice(0, 12).map(node => <button type="button" className="resultCard" key={node.id} onClick={() => void explainNode(node)}><span>{node.kind}</span><strong>{node.label}</strong><p>{node.content || `${node.materialUri}${node.locator ? `#${node.locator}` : ""}`}</p></button>)}
            {(results.memories?.length ?? 0) + (results.nodes?.length ?? 0) === 0 && <p className="empty">관련 맥락을 찾지 못했습니다.</p>}
          </div>}

          <div className="toolGrid">
            <form className="contextTool" onSubmit={event => void findPath(event)}>
              <div><p className="eyebrow">PATH</p><h2>두 실물 사이의 관계</h2></div>
              <label htmlFor="pathSource">시작 노드</label><input id="pathSource" value={pathSource} onChange={event => setPathSource(event.target.value)} placeholder="예: AGENTS.md" />
              <label htmlFor="pathTarget">도착 노드</label><input id="pathTarget" value={pathTarget} onChange={event => setPathTarget(event.target.value)} placeholder="예: Service.Update()" />
              <button disabled={busy}>경로 찾기</button>
              {pathResult && <ol className="pathResult">{(pathResult.nodes ?? []).map((node, index) => <li key={node.id}><strong>{node.label}</strong>{pathResult.edges?.[index] && <span>{pathResult.edges[index].relation}</span>}</li>)}</ol>}
            </form>
            <form className="contextTool" onSubmit={event => void prepareContext(event)}>
              <div><p className="eyebrow">PREPARE</p><h2>Agent 전달 맥락 미리보기</h2></div>
              <label htmlFor="prepareMessage">작업 의도</label><textarea id="prepareMessage" value={prepareMessage} onChange={event => setPrepareMessage(event.target.value)} placeholder="예: 업데이트가 의도와 Material의 연결을 보존하는지 확인해줘" />
              <button disabled={busy}>Context 준비</button>
              {prepared && <div className="prepared"><span>{prepared.action}</span><pre>{prepared.context?.rendered || prepared.clarification || "전달할 맥락이 없습니다."}</pre></div>}
            </form>
          </div>
        </section>}

        {page === "graph" && <section className="panel">
          <div className="sectionTitle"><div><p className="eyebrow">MATERIAL GRAPH</p><h2>실물과 지식의 구조</h2></div><span>{materialGraph?.totalNodes ?? 0} nodes · {materialGraph?.totalEdges ?? 0} edges</span></div>
          <form className="search" onSubmit={event => void loadGraph(event)}>
            <label htmlFor="graphScope">선택 범위</label>
            <div><input id="graphScope" value={graphScope} onChange={event => setGraphScope(event.target.value)} placeholder="전체 또는 예: internal/app" /><button disabled={busy}>그래프 불러오기</button></div>
          </form>
          <div className="contextGraph graphExplorer">
            <GraphView nodes={materialGraph?.nodes ?? []} edges={materialGraph?.edges ?? []} selectedID={selectedNode?.id} onSelect={node => void explainNode(node)} />
            <NodeDetails node={selectedNode} explanation={explanation} onSelect={node => void explainNode(node)} />
          </div>
        </section>}

        {page === "inbox" && <section className="panel operationsPanel">
          <div className="sectionTitle"><div><p className="eyebrow">OPERATIONS</p><h2>Context와 메모리 운영</h2></div><span>{requests.length + reviews.length} items need attention</span></div>
          <div className="operationsGrid">
            <section className="operationCard">
              <div className="cardTitle"><div><p className="eyebrow">REQUESTS</p><h3>해결할 Context 요청</h3></div><strong>{requests.length}</strong></div>
              {requests.length === 0 ? <p className="empty">열린 Context 요청이 없습니다.</p> : <div className="operationList">{requests.map(request => <article key={request.id}>
                <div className="itemMeta"><span>#{request.id} · {relativeTime(request.createdAt)}</span><span>{request.sessionId}</span></div>
                <p>{request.need}</p>
                <form className="inlineForm" onSubmit={event => void resolveRequest(event, request.id)}>
                  <label className="srOnly" htmlFor={`request-memory-${request.id}`}>연결할 메모리</label>
                  <select id={`request-memory-${request.id}`} name="memory" defaultValue="" required><option value="" disabled>해결 근거 선택</option>{memories.map(item => <option key={item.key} value={item.key}>{item.key}</option>)}</select>
                  <button disabled={busy || memories.length === 0}>해결</button>
                </form>
              </article>)}</div>}
            </section>

            <section className="operationCard">
              <div className="cardTitle"><div><p className="eyebrow">NEEDS REVIEW</p><h3>확인이 필요한 메모리</h3></div><strong>{reviews.length}</strong></div>
              {reviews.length === 0 ? <p className="empty">검토할 메모리가 없습니다.</p> : <div className="operationList">{reviews.map(review => <article key={review.id}>
                <div className="itemMeta"><span>{review.sourceType} · {relativeTime(review.createdAt)}</span><span>#{review.id}</span></div>
                <strong>{review.key}</strong><p>{review.reason}</p><small>{review.sourceId}</small>
                <div className="itemActions"><button className="secondary" disabled={busy} onClick={() => void resolveReview(review.id)}>현재 내용 유지</button><button className="textButton" onClick={() => setPage("memory")}>내용 확인</button></div>
                <details className="reviewChange"><summary>내용 수정 후 해결</summary><form onSubmit={event => void changeReview(event, review)}>
                  <label htmlFor={`review-kind-${review.id}`}>종류</label><select id={`review-kind-${review.id}`} name="kind" defaultValue={memories.find(item => item.key === review.key)?.kind || "note"}><option value="note">지식</option><option value="decision">결정</option><option value="reference">참조</option></select>
                  <label htmlFor={`review-value-${review.id}`}>새 내용</label><textarea id={`review-value-${review.id}`} name="value" defaultValue={memories.find(item => item.key === review.key)?.value || ""} required />
                  <button disabled={busy}>수정 적용</button>
                </form></details>
              </article>)}</div>}
            </section>

            <section className="operationCard auditCard">
              <div className="cardTitle"><div><p className="eyebrow">DECISION AUDIT</p><h3>최근 Prepare 판단</h3></div><strong>{decisions.length}</strong></div>
              {decisions.length === 0 ? <p className="empty">기록된 Prepare 판단이 없습니다.</p> : <div className="auditList">{decisions.map(decision => <details key={decision.id}>
                <summary><span className={`action ${decision.finalAction}`}>{decision.finalAction}</span><strong>{decision.inputText || `입력 해시 ${decision.inputHash.slice(0, 12)}`}</strong><small>{relativeTime(decision.createdAt)} · {decision.modelId || "deterministic"}</small></summary>
                <div className="auditBody"><p>{decision.proposal.reasonCode} · {(decision.delivery ?? []).map(item => item.key).join(", ") || "전달 없음"}</p>
                  <form className="feedbackForm" onSubmit={event => void submitFeedback(event, decision.id)}>
                    <label htmlFor={`verdict-${decision.id}`}>판단 평가</label><select id={`verdict-${decision.id}`} name="verdict" defaultValue={decision.feedback?.verdict || "correct"}><option value="correct">맞음</option><option value="incorrect">수정 필요</option></select>
                    <label htmlFor={`expected-${decision.id}`}>기대한 동작</label><select id={`expected-${decision.id}`} name="expectedAction" defaultValue={decision.feedback?.expectedAction || ""}><option value="">해당 없음</option><option value="skip">skip</option><option value="retrieve">retrieve</option><option value="ask">ask</option></select>
                    <label htmlFor={`keys-${decision.id}`}>기대한 메모리 키</label><input id={`keys-${decision.id}`} name="expectedKeys" defaultValue={decision.feedback?.expectedKeys?.join(", ") || ""} placeholder="쉼표로 구분" />
                    <label htmlFor={`note-${decision.id}`}>메모</label><input id={`note-${decision.id}`} name="note" defaultValue={decision.feedback?.note || ""} placeholder="선택 사항" />
                    <button disabled={busy}>피드백 저장</button>
                  </form>
                </div>
              </details>)}</div>}
            </section>

          </div>
        </section>}

        {page === "memory" && <section className="columns">
          <div className="panel">
            <div className="sectionTitle"><div><p className="eyebrow">REMEMBER</p><h2>메모리 추가</h2></div></div>
            <form className="memoryForm" onSubmit={event => void remember(event)}>
              <label htmlFor="kind">종류</label><select id="kind" value={kind} onChange={event => setKind(event.target.value)}><option value="note">지식</option><option value="decision">결정</option><option value="reference">참조</option></select>
              <label htmlFor="key">키</label><input id="key" value={key} onChange={event => setKey(event.target.value)} placeholder="decision.database" />
              <label htmlFor="value">내용</label><textarea id="value" value={value} onChange={event => setValue(event.target.value)} placeholder="왜 이 결정을 내렸는지 함께 기록하세요." />
              <button disabled={busy}>기억하기</button>
            </form>
          </div>
          <div className="panel memoryList">
            <div className="sectionTitle"><div><p className="eyebrow">DURABLE CONTEXT</p><h2>저장된 메모리</h2></div><span>{memories.length}</span></div>
            {memories.length === 0 ? <p className="empty">아직 저장된 메모리가 없습니다.</p> : memories.map(item => <article key={item.key}><span>{item.kind}</span><strong>{item.key}</strong><p>{item.value ?? item.source}</p><small>{relativeTime(item.updatedAt)}</small><div className="itemActions"><button className="secondary" disabled={busy} onClick={() => void confirmMemory(item.key)}>유효함</button><button className="danger" disabled={busy} onClick={() => void deleteMemory(item.key)}>삭제</button></div></article>)}
          </div>
        </section>}

        {page === "settings" && <section className="settingsGrid">
          <section className="panel modelCard">
            <div className="cardTitle"><div><p className="eyebrow">LOCAL MODELS</p><h2>Ollama와 Embedding</h2></div><span className={model?.available ? "status online" : "status"}>{model?.available ? "ONLINE" : "OPTIONAL"}</span></div>
            <p className="settingsIntro">로컬 모델은 선택 사항입니다. 구조 분석과 기본 검색은 모델 없이도 동작합니다.</p>
            <div className="modelSummary"><p><strong>{model?.version || "Ollama 미연결"}</strong><span>Embedding {embeddingStatus?.current ?? 0}개 최신 · {embeddingStatus?.pending ?? 0}개 대기</span></p><button className="secondary" disabled={busy} onClick={() => void startModels()}>Ollama 시작</button><button disabled={busy || !model?.available || (embeddingStatus?.pending ?? 0) === 0} onClick={() => void syncEmbeddings()}>Embedding 동기화</button></div>
            <div className="modelRoles">{(modelState?.selected ?? []).map(selected => <form key={`${selected.role}-${selected.model}`} onSubmit={event => void selectModel(event)}>
              <input type="hidden" name="role" value={selected.role} /><label htmlFor={`model-${selected.role}`}>{selected.role} <small>{selected.source === "project" ? "project · fixed" : selected.source}</small></label><div><input id={`model-${selected.role}`} name="model" defaultValue={selected.model || ""} placeholder="모델 태그" disabled={selected.role === "embedding" && selected.source === "project"} /><button className="secondary" disabled={busy || (selected.role === "embedding" && selected.source === "project")}>선택</button></div>
            </form>)}</div>
            <form className="installForm" onSubmit={event => void installModel(event)}><label htmlFor="install-model">모델 설치</label><div><input id="install-model" name="installModel" placeholder="예: qwen3:4b" required /><select name="installRole" aria-label="설치 후 사용할 역할"><option value="">설치만</option><option value="gate">gate</option><option value="reconcile">reconcile</option><option value="embedding">embedding</option></select><button disabled={busy}>설치</button></div></form>
          </section>
          <aside className="panel settingsNote"><p className="eyebrow">DESIGN PRINCIPLE</p><h2>Private by default</h2><p>프로젝트의 구조와 메모리는 사용자 전역 SQLite 저장소에 보관되며, 선택한 로컬 모델만 보조적으로 사용합니다.</p></aside>
        </section>}
        </div>
      </main>
    </div>
  );
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
