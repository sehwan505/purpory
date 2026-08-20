import { useMemo, useState, type FormEvent } from "react";
import type { app, graph, memory, prepare, project, reconcile } from "../wailsjs/go/models";

export type Page = "overview" | "workspace" | "reconcile" | "search" | "graph" | "projects" | "settings";

export function ProjectPicker({ current, projects, busy, onSelect, onManage }: {
  current?: project.Project;
  projects: project.Project[];
  busy: boolean;
  onSelect: (projectID: string) => void;
  onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  const currentID = current?.id ?? "";
  return <div className={`projectPicker${open ? " open" : ""}`} onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <button type="button" className="projectPickerToggle" aria-label="현재 프로젝트 선택" aria-expanded={open} onClick={() => setOpen(value => !value)}>
      <span className="projectMark" aria-hidden="true">{current?.name?.slice(0, 1).toUpperCase() || "P"}</span>
      <span className="projectCurrent"><small>CURRENT PROJECT</small><strong>{current?.name ?? "Project 없음"}</strong><em>{current ? "Project 범위 데이터" : "Global에서 Project를 만드세요"}</em></span>
      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4" /></svg>
    </button>
    {open && <div className="projectMenu">
      <div className="projectMenuTitle"><strong>프로젝트</strong><span>{projects.length}개 등록됨</span></div>
      <div className="projectOptions" role="menu">{projects.map(item => <button type="button" role="menuitemradio" aria-checked={item.id === currentID} key={item.id} disabled={busy} onClick={() => {
        setOpen(false);
        if (item.id !== currentID) onSelect(item.id);
      }}>
        <span className="projectMark" aria-hidden="true">{item.name.slice(0, 1).toUpperCase() || "P"}</span>
        <span><strong>{item.name}</strong><small>Project 범위</small></span>
        {item.id === currentID && <b>현재</b>}
      </button>)}</div>
      <button type="button" className="projectManage" onClick={() => { setOpen(false); onManage(); }}>Projects 관리 →</button>
    </div>}
  </div>;
}

export function NavIcon({ page }: { page: Page }) {
  const paths: Record<Page, string> = {
    overview: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    workspace: "M4 5h6l2 2h8v12H4z",
    reconcile: "M20 7h-5V2M4 17h5v5M18 12a7 7 0 0 0-12-5l-2 2M6 12a7 7 0 0 0 12 5l2-2",
    search: "M11 5a6 6 0 1 0 0 12 6 6 0 0 0 0-12m5 11 4 4",
    graph: "M6 5a2 2 0 1 0 0 .01M18 7a2 2 0 1 0 0 .01M8 18a2 2 0 1 0 0 .01M16 16a2 2 0 1 0 0 .01M7.7 6.1l8.6.8M7 6.8l1 9.3m2-1 6-6",
    projects: "M4 5h16v14H4zM8 9h8M8 13h5",
    settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4",
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d={paths[page]} /></svg>;
}

export function GraphView({ nodes, edges, selectedID, onSelect }: {
  nodes: graph.Node[];
  edges: graph.Edge[];
  selectedID?: string;
  onSelect: (node: graph.Node) => void;
}) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("");
  const [focused, setFocused] = useState(false);
  const [zoom, setZoom] = useState(1);
  const kinds = useMemo(() => [...new Set(nodes.map(node => node.kind))].sort(), [nodes]);
  const neighborIDs = useMemo(() => {
    const result = new Set(selectedID ? [selectedID] : []);
    if (selectedID) for (const edge of edges) {
      if (edge.sourceId === selectedID) result.add(edge.targetId);
      if (edge.targetId === selectedID) result.add(edge.sourceId);
    }
    return result;
  }, [edges, selectedID]);
  const normalizedQuery = query.trim().toLowerCase();
  const visibleNodes = nodes.filter(node => (!kind || node.kind === kind) && (!normalizedQuery || `${node.label} ${node.content}`.toLowerCase().includes(normalizedQuery)) && (!focused || !selectedID || neighborIDs.has(node.id))).slice(0, 80);
  if (nodes.length === 0) return <div className="graphEmpty">표시할 구조가 없습니다. 먼저 프로젝트를 업데이트해 주세요.</div>;
  const positions = new Map(visibleNodes.map((node, index) => [node.id, {
    x: 240 + Math.cos(index / visibleNodes.length * Math.PI * 2) * (118 + index % 3 * 24),
    y: 175 + Math.sin(index / visibleNodes.length * Math.PI * 2) * (98 + index % 2 * 22),
  }]));
  const visibleEdges = edges.filter(edge => positions.has(edge.sourceId) && positions.has(edge.targetId)).slice(0, 96);

  const width = 480 / zoom;
  const height = 350 / zoom;
  return <div className="graphCanvas">
    <div className="graphControls">
      <label><span className="srOnly">노드 검색</span><input value={query} onChange={event => setQuery(event.target.value)} placeholder="노드 검색" /></label>
      <label><span className="srOnly">노드 종류</span><select value={kind} onChange={event => setKind(event.target.value)}><option value="">모든 종류</option>{kinds.map(value => <option key={value}>{value}</option>)}</select></label>
      <button type="button" className={focused ? "active" : ""} disabled={!selectedID} onClick={() => setFocused(value => !value)}>이웃만</button>
      <button type="button" aria-label="축소" disabled={zoom <= .75} onClick={() => setZoom(value => Math.max(.75, value - .25))}>−</button>
      <button type="button" aria-label="화면 맞춤" onClick={() => setZoom(1)}>맞춤</button>
      <button type="button" aria-label="확대" disabled={zoom >= 2} onClick={() => setZoom(value => Math.min(2, value + .25))}>+</button>
    </div>
    <div className="graphStats">{visibleNodes.length}/{nodes.length} nodes · {visibleEdges.length}/{edges.length} edges</div>
    <div className="graphLegend" aria-label="노드 색상 범례">
      {(["intent", "material", "knowledge"] as const).map(kind => <span key={kind}><i aria-hidden="true" style={{ background: nodeColor(kind) }} />{kind}</span>)}
    </div>
    {visibleNodes.length === 0 && <div className="graphFilteredEmpty">필터에 맞는 노드가 없습니다.</div>}
    <svg viewBox={`${(480 - width) / 2} ${(350 - height) / 2} ${width} ${height}`} role="img" aria-label="Intent, Material, Knowledge 관계 그래프">
      <defs><pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M 20 0 L 0 0 0 20" fill="none" stroke="#dfe3da" strokeWidth=".5" /></pattern></defs>
      <rect width="480" height="350" fill="url(#grid)" />
      {visibleEdges.map((edge, index) => {
        const source = positions.get(edge.sourceId)!;
        const target = positions.get(edge.targetId)!;
        return <line key={`${edge.sourceId}-${edge.targetId}-${index}`} x1={source.x} y1={source.y} x2={target.x} y2={target.y} stroke="#9aa696" strokeOpacity=".6" strokeWidth=".8"><title>{edge.relation}</title></line>;
      })}
      {visibleNodes.map(node => {
        const position = positions.get(node.id)!;
        const selected = node.id === selectedID;
        const color = nodeColor(node.kind, node.state);
        return <g className="graphNode" key={node.id} role="button" tabIndex={0} aria-label={`${node.kind} ${node.label}`} onClick={() => onSelect(node)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") onSelect(node); }}>
          <circle cx={position.x} cy={position.y} r={selected ? 7 : 5} fill={color} stroke={selected ? "#1a2019" : color} strokeWidth={selected ? 2 : 1} />
          <circle cx={position.x} cy={position.y} r="10" fill="none" stroke={color} strokeOpacity=".2" />
          <text x={position.x + 10} y={position.y + 3} fill="#4e584b" fontSize="7">{node.label.slice(0, 26)}</text>
          <title>{node.label} · {node.kind}{node.subkind ? `/${node.subkind}` : ""}{node.state === "missing" ? " · missing" : ""}</title>
        </g>;
      })}
    </svg>
  </div>;
}

export function NodeDetails({ node, explanation, durable, busy, onSelect, onEdit, onConfirm, onDelete }: {
  node?: graph.Node;
  explanation?: app.ExplainResult;
  durable?: memory.Memory;
  busy?: boolean;
  onSelect: (node: graph.Node) => void;
  onEdit?: (value: memory.Memory) => void;
  onConfirm?: (key: string) => void;
  onDelete?: (key: string) => void;
}) {
  if (!node) return <div className="nodeDetails empty">그래프에서 노드를 선택하면 근거와 관계를 볼 수 있습니다.</div>;
  const connections = explanation?.graph?.connections ?? [];
  return <div className="nodeDetails">
    <span>{node.kind}{node.subkind ? `/${node.subkind}` : ""}{node.state === "missing" ? " · missing" : ""}</span>
    <h3>{node.label}</h3>
    <p className="nodeLocation">{node.materialUri}{node.locator ? `#${node.locator}` : ""}</p>
    {node.content && <p>{node.content}</p>}
    {durable && <div className="memoryActions"><button type="button" disabled={busy} onClick={() => onEdit?.(durable)}>편집</button><button type="button" className="secondary" disabled={busy} onClick={() => onConfirm?.(durable.key)}>유효함</button><button type="button" className="danger" disabled={busy} onClick={() => onDelete?.(durable.key)}>삭제</button></div>}
    <strong className="connectionTitle">연결 {connections.length}</strong>
    <div className="connections">{connections.length === 0 ? <p className="empty">직접 연결된 노드가 없습니다.</p> : connections.map(connection => <button type="button" key={`${connection.direction}-${connection.relation}-${connection.node.id}`} onClick={() => onSelect(connection.node)}><span>{connection.direction === "out" ? "→" : "←"} {connection.relation}</span><strong>{connection.node.label}</strong></button>)}</div>
  </div>;
}

function nodeColor(kind: string, state = "active") {
  if (state === "missing") return "#a33b32";
  if (kind === "intent") return "#b12a63";
  if (kind === "material") return "#3973a5";
  if (kind === "knowledge") return "#5f7358";
  if (kind === "reference") return "#786247";
  if (kind === "section") return "#97651b";
  if (kind === "type") return "#7559a2";
  if (kind === "function") return "#287b72";
  return "#5f7358";
}

export function ResourceAssignments({ observations, projects, currentID, busy, onCreate, onSelect, onAssign, onUnassign }: {
  observations: project.Observation[];
  projects: project.Project[];
  currentID?: string;
  busy: boolean;
  onCreate: (event: FormEvent<HTMLFormElement>) => void;
  onSelect: (projectID: string) => void;
  onAssign: (projectID: string, resourceID: string) => void;
  onUnassign: (projectID: string, resourceID: string) => void;
}) {
  return <section className="projectManagement">
    <section className="panel createProjectPanel">
      <div><p className="eyebrow">GLOBAL PROJECTS</p><h2>Project 만들기</h2><p>Project는 Repository와 독립적인 작업 범위입니다. 만든 뒤 필요한 Repository를 연결하세요.</p></div>
      <form onSubmit={onCreate}><label htmlFor="project-name">Project 이름</label><div><input id="project-name" name="name" maxLength={120} placeholder="예: Purpory Desktop" required /><button disabled={busy}>만들기</button></div></form>
    </section>
    <section className="panel assignmentPanel">
    <div className="sectionTitle"><div><p className="eyebrow">OBSERVED REPOSITORIES</p><h2>Hook에서 발견한 Repository</h2></div><span>{observations.length}</span></div>
    <p className="panelIntro">Repository를 Project에 드롭하거나 목록에서 추가하세요. 연결 전에는 경로 정보만 보관됩니다.</p>
    <div className="assignmentLayout">
      <div className="observationList">{observations.length === 0 ? <p className="empty">아직 Hook에서 발견한 Repository가 없습니다.</p> : observations.map(value => {
        const availableProjects = projects.filter(item => !(value.projectIds ?? []).includes(item.id));
        return <article key={value.resource.id} draggable onDragStart={event => event.dataTransfer.setData("text/plain", value.resource.id)}>
          <span>{value.resource.provider} · {relativeTime(value.observedAt)}</span><strong>{value.resource.label}</strong><small>{value.resource.views?.[0]?.root ?? value.resource.identity}</small>
          <p>{(value.projectIds ?? []).length === 0 ? "소속된 Project 없음" : `${value.projectIds.length}개 Project에 소속됨`}</p>
          {availableProjects.length > 0 && <form onSubmit={event => { event.preventDefault(); const projectID = String(new FormData(event.currentTarget).get("project") ?? ""); if (projectID) onAssign(projectID, value.resource.id); }}>
            <select name="project" aria-label={`${value.resource.label}을 추가할 프로젝트`} defaultValue={availableProjects[0]?.id}>{availableProjects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select>
            <button disabled={busy}>추가</button>
          </form>}
        </article>;
      })}</div>
      <div className="projectDropList" aria-label="Repository를 추가할 Project">{projects.length === 0 ? <p className="empty">먼저 Project를 만들어 주세요.</p> : projects.map(item => {
        const assigned = observations.filter(value => (value.projectIds ?? []).includes(item.id));
        return <div className={item.id === currentID ? "current" : ""} key={item.id} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); const resourceID = event.dataTransfer.getData("text/plain"); if (resourceID) onAssign(item.id, resourceID); }}>
          <span>PROJECT {item.id === currentID ? "· CURRENT" : ""}</span><strong>{item.name}</strong><p>여기에 Repository 놓기 · {assigned.length}개 연결됨</p>
          <div className="assignedResources">{assigned.map(value => <div key={value.resource.id}><small>{value.resource.label}</small><button type="button" aria-label={`${item.name}에서 ${value.resource.label} 연결 해제`} disabled={busy} onClick={() => onUnassign(item.id, value.resource.id)}>해제</button></div>)}</div>
          {item.id !== currentID && <button type="button" className="textButton" disabled={busy} onClick={() => onSelect(item.id)}>이 Project 열기 →</button>}
        </div>;
      })}</div>
    </div>
    </section>
  </section>;
}

export function ReconciliationQueue({ runs }: { runs: reconcile.Run[] }) {
  const working = runs.filter(run => !["queued", "completed", "failed"].includes(run.phase));
  const waiting = runs.filter(run => run.phase === "queued").sort((left, right) => Date.parse(left.queuedAt) - Date.parse(right.queuedAt));
  const history = runs.filter(run => run.phase === "completed" || run.phase === "failed").sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt));
  return <section className="reconcileQueue">
    <div className="queueBoard">
    <section className="panel queueColumn queuedColumn">
      <header className="queueColumnHeader"><QueueStageIcon stage="queued" /><div><small>STEP 1</small><h2>Queued</h2><p>먼저 등록된 작업부터 처리합니다.</p></div><b>{waiting.length}</b></header>
      {waiting.length === 0 ? <p className="empty">대기 중인 작업이 없습니다.</p> : <div className="queueList">{waiting.map((run, index) => <QueueRun key={run.id} run={run} position={index + 1} />)}</div>}
    </section>
    <span className="queueFlowArrow" aria-hidden="true">→</span>
    <section className="panel queueColumn processingColumn">
      <header className="queueColumnHeader"><QueueStageIcon stage="processing" /><div><small>STEP 2</small><h2>Processing</h2><p>현재 Worker의 처리 단계입니다.</p></div><b>{working.length}</b></header>
      {working.length === 0 ? <p className="empty">Worker가 처리 중인 작업이 없습니다.</p> : <div className="queueList">{working.map(run => <QueueRun key={run.id} run={run} />)}</div>}
    </section>
    <span className="queueFlowArrow" aria-hidden="true">→</span>
    <section className="panel queueColumn doneColumn">
      <header className="queueColumnHeader"><QueueStageIcon stage="done" /><div><small>STEP 3</small><h2>Done</h2><p>최근 완료 또는 실패한 작업입니다.</p></div><b>{history.length}</b></header>
      {history.length === 0 ? <p className="empty">처리 기록이 없습니다.</p> : <div className="queueList">{history.map(run => <QueueRun key={run.id} run={run} />)}</div>}
    </section>
    </div>
  </section>;
}

function QueueStageIcon({ stage }: { stage: "queued" | "processing" | "done" }) {
  const paths = {
    queued: "M12 3a9 9 0 1 0 9 9M12 7v5l3 2",
    processing: "M20 7h-5V2M4 17h5v5M18 12a7 7 0 0 0-12-5l-2 2M6 12a7 7 0 0 0 12 5l2-2",
    done: "m5 12 4 4L19 6",
  };
  return <span className={`queueStageIcon ${stage}`} aria-hidden="true"><svg viewBox="0 0 24 24"><path d={paths[stage]} /></svg></span>;
}

const reconciliationPhases = ["running", "reading", "updating", "proposing", "applying"];

function QueueRun({ run, position }: { run: reconcile.Run; position?: number }) {
  const phase = reconciliationPhases.indexOf(run.phase);
  const marker = position ?? (run.phase === "completed" ? "✓" : run.phase === "failed" ? "!" : "●");
  return <article className={`queueRun ${run.phase}`}>
    <b className="queuePosition" aria-label={position ? `대기 순서 ${position}` : undefined}>{marker}</b>
    <div className="queueRunBody">
      <div className="queueRunHead"><span>{run.agent} · {run.reason || "session end"}</span><b>{reconciliationLabel(run.phase)}</b></div>
      <strong title={run.cwd}>{compactPath(run.cwd)}</strong>
      <p>{run.detail || "진행 정보 대기 중"}</p>
      <small>{run.sessionId} · 등록 {relativeTime(run.queuedAt)} · 갱신 {relativeTime(run.updatedAt)}</small>
      {phase >= 0 && <div className="queueProgress" aria-label={`현재 단계 ${reconciliationLabel(run.phase)}`}>{reconciliationPhases.map((value, index) => <i key={value} className={index <= phase ? "reached" : ""} />)}</div>}
    </div>
  </article>;
}

export function WorkspaceAttention({ requests, reviews, memories, busy, onResolveRequest, onResolveReview, onChangeReview, onOpenMemory }: {
  requests: prepare.ContextRequest[];
  reviews: memory.Review[];
  memories: memory.Memory[];
  busy: boolean;
  onResolveRequest: (event: FormEvent<HTMLFormElement>, id: number) => void;
  onResolveReview: (id: number) => void;
  onChangeReview: (event: FormEvent<HTMLFormElement>, review: memory.Review) => void;
  onOpenMemory: (key: string) => void;
}) {
  return <section className="panel operationsPanel">
    <div className="sectionTitle"><div><p className="eyebrow">ATTENTION</p><h2>확인이 필요한 항목</h2></div><span>{requests.length + reviews.length}</span></div>
    <div className="operationsGrid compactOperations">
      <section className="operationCard"><div className="cardTitle"><div><p className="eyebrow">REQUESTS</p><h3>Context 요청</h3></div><strong>{requests.length}</strong></div>
        {requests.length === 0 ? <p className="empty">열린 Context 요청이 없습니다.</p> : <div className="operationList">{requests.map(request => <article key={request.id}>
          <div className="itemMeta"><span>#{request.id} · {relativeTime(request.createdAt)}</span><span>{request.sessionId}</span></div><p>{request.need}</p>
          <form className="inlineForm" onSubmit={event => onResolveRequest(event, request.id)}><select name="memory" aria-label="해결 근거" defaultValue="" required><option value="" disabled>해결 근거 선택</option>{memories.map(item => <option key={item.key} value={item.key}>{item.key}</option>)}</select><button disabled={busy || memories.length === 0}>해결</button></form>
        </article>)}</div>}
      </section>
      <section className="operationCard"><div className="cardTitle"><div><p className="eyebrow">NEEDS REVIEW</p><h3>메모리 검토</h3></div><strong>{reviews.length}</strong></div>
        {reviews.length === 0 ? <p className="empty">검토할 메모리가 없습니다.</p> : <div className="operationList">{reviews.map(review => <article key={review.id}>
          <div className="itemMeta"><span>{review.sourceType} · {relativeTime(review.createdAt)}</span><span>#{review.id}</span></div><strong>{review.key}</strong><p>{review.reason}</p><small>{review.sourceId}</small>
          <div className="itemActions"><button className="secondary" disabled={busy} onClick={() => onResolveReview(review.id)}>현재 내용 유지</button><button className="textButton" onClick={() => onOpenMemory(review.key)}>Graph에서 확인</button></div>
          <details className="reviewChange"><summary>내용 수정 후 해결</summary><form onSubmit={event => onChangeReview(event, review)}><label>종류<select name="kind" defaultValue={memories.find(item => item.key === review.key)?.kind || "note"}><option value="note">지식</option><option value="decision">결정</option><option value="reference">참조</option></select></label><label>새 내용<textarea name="value" defaultValue={memories.find(item => item.key === review.key)?.value || ""} required /></label><button disabled={busy}>수정 적용</button></form></details>
        </article>)}</div>}
      </section>
    </div>
  </section>;
}

export function WorkspaceHistory({ requests, reviews, decisions, busy, onFeedback }: {
  requests: prepare.ContextRequest[];
  reviews: memory.Review[];
  decisions: prepare.Decision[];
  busy: boolean;
  onFeedback: (event: FormEvent<HTMLFormElement>, id: number) => void;
}) {
  return <section className="panel operationsPanel">
    <div className="sectionTitle"><div><p className="eyebrow">ACTIVITY HISTORY</p><h2>요청·검토·Prepare 기록</h2></div><span>{requests.length + reviews.length + decisions.length}</span></div>
    <div className="operationsGrid">
      <section className="operationCard"><div className="cardTitle"><h3>해결된 Context 요청</h3><strong>{requests.length}</strong></div><div className="operationList">{requests.length === 0 ? <p className="empty">해결 기록이 없습니다.</p> : requests.map(item => <article key={item.id}><div className="itemMeta"><span>#{item.id}</span><span>{relativeTime(item.resolvedAt || item.createdAt)}</span></div><p>{item.need}</p><strong>{item.resolvedKey || "근거 없음"}</strong></article>)}</div></section>
      <section className="operationCard"><div className="cardTitle"><h3>완료된 메모리 검토</h3><strong>{reviews.length}</strong></div><div className="operationList">{reviews.length === 0 ? <p className="empty">검토 기록이 없습니다.</p> : reviews.map(item => <article key={item.id}><div className="itemMeta"><span>#{item.id} · {item.outcome}</span><span>{relativeTime(item.resolvedAt || item.createdAt)}</span></div><strong>{item.key}</strong><p>{item.reason}</p></article>)}</div></section>
      <section className="operationCard auditCard"><div className="cardTitle"><h3>최근 Prepare 판단</h3><strong>{decisions.length}</strong></div>
        {decisions.length === 0 ? <p className="empty">판단 기록이 없습니다.</p> : <div className="auditList">{decisions.map(decision => <details key={decision.id}><summary><span className={`action ${decision.finalAction}`}>{decision.finalAction}</span><strong>{decision.inputText || `입력 해시 ${decision.inputHash.slice(0, 12)}`}</strong><small>{relativeTime(decision.createdAt)}</small></summary><div className="auditBody"><p>{decision.proposal.reasonCode} · {(decision.hints?.nodes ?? []).map(item => item.path || item.id).join(", ") || "힌트 없음"}</p><form className="feedbackForm" onSubmit={event => onFeedback(event, decision.id)}><label>판단 평가<select name="verdict" defaultValue={decision.feedback?.verdict || "correct"}><option value="correct">맞음</option><option value="incorrect">수정 필요</option></select></label><label>기대한 동작<select name="expectedAction" defaultValue={decision.feedback?.expectedAction || ""}><option value="">해당 없음</option><option value="skip">skip</option><option value="retrieve">retrieve</option><option value="ask">ask</option></select></label><label>기대한 메모리 키<input name="expectedKeys" defaultValue={decision.feedback?.expectedKeys?.join(", ") || ""} /></label><label>메모<input name="note" defaultValue={decision.feedback?.note || ""} /></label><button disabled={busy}>피드백 저장</button></form></div></details>)}</div>}
      </section>
    </div>
  </section>;
}

export function WorkspaceTopology({ workspace, selectedViewID, history = false, onSelectView }: {
  workspace: project.Workspace;
  selectedViewID: string;
  history?: boolean;
  onSelectView: (id: string) => void;
}) {
  const views = workspace.resources.flatMap(resource => resource.views ?? []);
  const visible = (session: project.Session) => isHistoricalSession(session) === history;
  const sessions = views.flatMap(view => (view.sessions ?? []).filter(visible));
  const unmapped = (workspace.unmappedSessions ?? []).filter(visible);
  const selected = views.find(view => view.id === selectedViewID) ?? views.find(view => (view.sessions ?? []).some(visible)) ?? views[0];
  const selectedResource = workspace.resources.find(resource => resource.views?.some(view => view.id === selected?.id));
  return <section className="workspaceLayout">
    <div className="panel workspaceBrowser">
      <div className="workspaceProject"><span>PROJECT</span><strong>{workspace.project.name}</strong><small>{workspace.resources.length}개 Repository의 작업 구조</small></div>
      <div className="workspaceCounts"><span>{workspace.resources.length} Repositories</span><span>{views.length} Views</span><span>{sessions.length + unmapped.length} Sessions</span></div>
      <div className="workspaceTree">{workspace.resources.map(resource => <section key={resource.id}>
        <div className="resourceHeading"><span>{resource.provider}</span><strong>{resource.label}</strong><small>{resource.identity}</small></div>
        {(resource.views ?? []).map(view => <button type="button" key={view.id} className={view.id === selected?.id ? "selected" : ""} onClick={() => onSelectView(view.id)}>
          <span className={`viewState${view.available ? "" : " missing"}`} /><div><strong>{view.branch || "folder / detached"}</strong><small>{compactPath(view.root)} · {(view.sessions ?? []).filter(visible).length} sessions</small></div>
        </button>)}
      </section>)}</div>
    </div>
    <div className="panel workspaceDetail">{selected ? <>
      <div className="viewHeader"><div><p className="eyebrow">{selectedResource?.label ?? "VIEW"}</p><h2>{selected.branch || "folder / detached"}</h2><p className="path">{selected.root}</p></div><span className={selected.available ? "status online" : "status"}>{selected.available ? "AVAILABLE" : "MISSING"}</span></div>
      <div className="viewFacts"><div><span>REVISION</span><strong>{selected.revision?.slice(0, 10) || "—"}</strong></div><div><span>WORKTREE</span><strong>{selected.dirty ? "변경 있음" : "Clean"}</strong></div><div><span>OBSERVED</span><strong>{relativeTime(selected.observedAt)}</strong></div></div>
      <div className="detailTitle"><div><p className="eyebrow">SESSIONS</p><h3>{history ? "24시간이 지난 작업 기록" : "현재와 최근 작업"}</h3></div><span>{(selected.sessions ?? []).filter(visible).length}</span></div>
      <div className="sessionList">{(selected.sessions ?? []).filter(visible).length === 0 ? <p className="empty">이 View에 표시할 Session이 없습니다.</p> : (selected.sessions ?? []).filter(visible).map(session => <SessionCard key={session.id} session={session} />)}</div>
    </> : <p className="empty">발견된 View가 없습니다. 프로젝트 업데이트를 실행해 주세요.</p>}</div>
    <details className="panel unmapped"><summary>View 연결을 확인할 과거 Session {unmapped.length}개</summary>{unmapped.length === 0 ? <p className="empty">View 연결을 확인할 과거 Session이 없습니다.</p> : <div className="unmappedGrid">{unmapped.map(session => <SessionCard key={session.id} session={session} />)}</div>}</details>
  </section>;
}

function isHistoricalSession(session: project.Session) {
  if (session.status === "active") return false;
  const updated = Date.parse(session.updatedAt);
  return Number.isFinite(updated) && Date.now() - updated >= 24 * 60 * 60 * 1000;
}

function SessionCard({ session }: { session: project.Session }) {
  const items = session.items ?? [];
  return <div className={`sessionNode ${session.status}`}><span>{session.agent} · {session.status}</span><strong>{session.id}</strong><small>{relativeTime(session.updatedAt)} · {items.length}개 Context 전달</small>
    {items.length > 0 && <details><summary>전달된 Context 보기</summary><ul>{items.map((item, index) => <li key={`${item.key}-${item.valueHash}-${index}`}><b>{item.label || item.key}</b><span>{item.kind || "context"}</span>{item.preview && <p>{item.preview}</p>}</li>)}</ul></details>}
  </div>;
}

export function reconciliationLabel(phase: string) {
  const labels: Record<string, string> = {
    queued: "Reconcile 대기", running: "Reconcile 시작", reading: "Transcript 확인",
    updating: "Material 최신화", proposing: "의도 후보 추출", applying: "메모리 반영",
    completed: "Reconcile 완료", failed: "Reconcile 실패",
  };
  return labels[phase] ?? phase;
}

export function relativeTime(value: string) {
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return "시간 정보 없음";
  const minutes = Math.max(0, Math.floor((Date.now() - time) / 60000));
  if (minutes < 1) return "방금 전";
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}

function compactPath(value: string) {
  const parts = value.split("/").filter(Boolean);
  return parts.length > 2 ? `…/${parts.slice(-2).join("/")}` : value;
}
