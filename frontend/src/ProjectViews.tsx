import type { app, graph, project, reconcile } from "../wailsjs/go/models";

export type Page = "overview" | "workspace" | "search" | "graph" | "inbox" | "memory" | "settings";

export function ProjectPicker({ current, projects, busy, onSelect }: {
  current?: project.Project;
  projects: project.Project[];
  busy: boolean;
  onSelect: (projectID: string) => void;
}) {
  const currentID = current?.id ?? "";
  return <details className="projectPicker" onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false; }}>
    <summary aria-label="현재 프로젝트 선택">
      <span className="projectMark" aria-hidden="true">{current?.name?.slice(0, 1).toUpperCase() || "P"}</span>
      <span className="projectCurrent"><small>CURRENT PROJECT</small><strong>{current?.name ?? "프로젝트 확인 중"}</strong><em title={current?.root}>{current?.root ?? "등록 정보를 불러오는 중"}</em></span>
      <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4" /></svg>
    </summary>
    <div className="projectMenu">
      <div className="projectMenuTitle"><strong>프로젝트</strong><span>{projects.length}개 등록됨</span></div>
      <div className="projectOptions" role="menu">{projects.map(item => <button type="button" role="menuitemradio" aria-checked={item.id === currentID} key={item.id} disabled={busy} onClick={event => {
        const details = event.currentTarget.closest("details");
        if (details) details.open = false;
        if (item.id !== currentID) onSelect(item.id);
      }}>
        <span className="projectMark" aria-hidden="true">{item.name.slice(0, 1).toUpperCase() || "P"}</span>
        <span><strong>{item.name}</strong><small title={item.root}>{item.root}</small></span>
        {item.id === currentID && <b>현재</b>}
      </button>)}</div>
      {projects.length < 2 && <p>전환하려면 다른 프로젝트를 먼저 등록하세요.</p>}
    </div>
  </details>;
}

export function NavIcon({ page }: { page: Page }) {
  const paths: Record<Page, string> = {
    overview: "M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z",
    workspace: "M4 5h6l2 2h8v12H4z",
    search: "M11 5a6 6 0 1 0 0 12 6 6 0 0 0 0-12m5 11 4 4",
    graph: "M6 5a2 2 0 1 0 0 .01M18 7a2 2 0 1 0 0 .01M8 18a2 2 0 1 0 0 .01M16 16a2 2 0 1 0 0 .01M7.7 6.1l8.6.8M7 6.8l1 9.3m2-1 6-6",
    inbox: "M4 5h16v14H4zM4 14h4l2 2h4l2-2h4",
    memory: "M6 4h9l3 3v13H6zM9 10h6M9 14h6",
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
  const visibleNodes = nodes.slice(0, 36);
  if (visibleNodes.length === 0) return <div className="graphEmpty">표시할 구조가 없습니다. 먼저 프로젝트를 업데이트해 주세요.</div>;
  const positions = new Map(visibleNodes.map((node, index) => [node.id, {
    x: 240 + Math.cos(index / visibleNodes.length * Math.PI * 2) * (118 + index % 3 * 24),
    y: 175 + Math.sin(index / visibleNodes.length * Math.PI * 2) * (98 + index % 2 * 22),
  }]));
  const visibleEdges = edges.filter(edge => positions.has(edge.sourceId) && positions.has(edge.targetId)).slice(0, 96);

  return <div className="graphCanvas">
    <div className="graphStats">{visibleNodes.length}/{nodes.length} nodes · {visibleEdges.length}/{edges.length} edges</div>
    <div className="graphLegend" aria-label="노드 색상 범례">
      {(["intent", "material", "knowledge"] as const).map(kind => <span key={kind}><i aria-hidden="true" style={{ background: nodeColor(kind) }} />{kind}</span>)}
    </div>
    <svg viewBox="0 0 480 350" role="img" aria-label="Intent, Material, Knowledge 관계 그래프">
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
        const color = nodeColor(node.kind);
        return <g className="graphNode" key={node.id} role="button" tabIndex={0} aria-label={`${node.kind} ${node.label}`} onClick={() => onSelect(node)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") onSelect(node); }}>
          <circle cx={position.x} cy={position.y} r={selected ? 7 : 5} fill={color} stroke={selected ? "#1a2019" : color} strokeWidth={selected ? 2 : 1} />
          <circle cx={position.x} cy={position.y} r="10" fill="none" stroke={color} strokeOpacity=".2" />
          <text x={position.x + 10} y={position.y + 3} fill="#4e584b" fontSize="7">{node.label.slice(0, 26)}</text>
          <title>{node.label} · {node.kind}</title>
        </g>;
      })}
    </svg>
  </div>;
}

export function NodeDetails({ node, explanation, onSelect }: {
  node?: graph.Node;
  explanation?: app.ExplainResult;
  onSelect: (node: graph.Node) => void;
}) {
  if (!node) return <div className="nodeDetails empty">그래프에서 노드를 선택하면 근거와 관계를 볼 수 있습니다.</div>;
  const connections = explanation?.graph?.connections ?? [];
  return <div className="nodeDetails">
    <span>{node.kind}</span>
    <h3>{node.label}</h3>
    <p className="nodeLocation">{node.materialUri}{node.locator ? `#${node.locator}` : ""}</p>
    {node.content && <p>{node.content}</p>}
    <strong className="connectionTitle">연결 {connections.length}</strong>
    <div className="connections">{connections.length === 0 ? <p className="empty">직접 연결된 노드가 없습니다.</p> : connections.map(connection => <button type="button" key={`${connection.direction}-${connection.relation}-${connection.node.id}`} onClick={() => onSelect(connection.node)}><span>{connection.direction === "out" ? "→" : "←"} {connection.relation}</span><strong>{connection.node.label}</strong></button>)}</div>
  </div>;
}

function nodeColor(kind: string) {
  if (kind === "intent") return "#b12a63";
  if (kind === "material") return "#3973a5";
  if (kind === "knowledge") return "#5f7358";
  if (kind === "reference") return "#786247";
  if (kind === "missing") return "#a33b32";
  if (kind === "section") return "#97651b";
  if (kind === "type") return "#7559a2";
  if (kind === "function") return "#287b72";
  return "#5f7358";
}

export function WorkspaceTopology({ workspace, reconciliations, selectedViewID, onSelectView }: {
  workspace: project.Workspace;
  reconciliations: reconcile.Run[];
  selectedViewID: string;
  onSelectView: (id: string) => void;
}) {
  const views = workspace.resources.flatMap(resource => resource.views ?? []);
  const sessions = views.flatMap(view => view.sessions ?? []);
  const unmapped = workspace.unmappedSessions ?? [];
  const reconciliationBySession = new Map<string, reconcile.Run>();
  for (const run of reconciliations) if (!reconciliationBySession.has(run.sessionId)) reconciliationBySession.set(run.sessionId, run);
  const selected = views.find(view => view.id === selectedViewID) ?? views.find(view => view.root === workspace.project.root) ?? views[0];
  const selectedResource = workspace.resources.find(resource => resource.views?.some(view => view.id === selected?.id));
  return <section className="workspaceLayout">
    <div className="panel workspaceBrowser">
      <div className="workspaceProject"><span>PROJECT</span><strong>{workspace.project.name}</strong><small>{workspace.project.root}</small></div>
      <div className="workspaceCounts"><span>{workspace.resources.length} Resources</span><span>{views.length} Views</span><span>{sessions.length + unmapped.length} Sessions</span></div>
      <div className="workspaceTree">{workspace.resources.map(resource => <section key={resource.id}>
        <div className="resourceHeading"><span>{resource.provider}</span><strong>{resource.label}</strong><small>{resource.identity}</small></div>
        {(resource.views ?? []).map(view => <button type="button" key={view.id} className={view.id === selected?.id ? "selected" : ""} onClick={() => onSelectView(view.id)}>
          <span className={`viewState${view.available ? "" : " missing"}`} /><div><strong>{view.branch || "folder / detached"}</strong><small>{compactPath(view.root)} · {view.sessions?.length ?? 0} sessions</small></div>{view.root === workspace.project.root && <b>현재</b>}
        </button>)}
      </section>)}</div>
    </div>
    <div className="panel workspaceDetail">{selected ? <>
      <div className="viewHeader"><div><p className="eyebrow">{selectedResource?.label ?? "VIEW"}</p><h2>{selected.branch || "folder / detached"}</h2><p className="path">{selected.root}</p></div><span className={selected.available ? "status online" : "status"}>{selected.available ? "AVAILABLE" : "MISSING"}</span></div>
      <div className="viewFacts"><div><span>REVISION</span><strong>{selected.revision?.slice(0, 10) || "—"}</strong></div><div><span>WORKTREE</span><strong>{selected.dirty ? "변경 있음" : "Clean"}</strong></div><div><span>OBSERVED</span><strong>{relativeTime(selected.observedAt)}</strong></div></div>
      <div className="detailTitle"><div><p className="eyebrow">SESSIONS</p><h3>이 View의 작업 기록</h3></div><span>{selected.sessions?.length ?? 0}</span></div>
      <div className="sessionList">{(selected.sessions ?? []).length === 0 ? <p className="empty">이 View에 기록된 Session이 없습니다.</p> : selected.sessions.map(session => <SessionCard key={session.id} session={session} reconciliation={reconciliationBySession.get(session.id)} />)}</div>
    </> : <p className="empty">발견된 View가 없습니다. 프로젝트 업데이트를 실행해 주세요.</p>}</div>
    <details className="panel unmapped"><summary>View 연결을 확인할 과거 Session {unmapped.length}개</summary>{unmapped.length === 0 ? <p className="empty">View 연결을 확인할 과거 Session이 없습니다.</p> : <div className="unmappedGrid">{unmapped.map(session => <SessionCard key={session.id} session={session} reconciliation={reconciliationBySession.get(session.id)} />)}</div>}</details>
  </section>;
}

function SessionCard({ session, reconciliation }: { session: project.Session; reconciliation?: reconcile.Run }) {
  const items = session.items ?? [];
  return <div className={`sessionNode ${session.status}`}><span>{session.agent} · {session.status}</span><strong>{session.id}</strong><small>{relativeTime(session.updatedAt)} · {items.length}개 Context 전달</small>
    {reconciliation && <div className={`reconciliation ${reconciliation.phase}`}><b>{reconciliationLabel(reconciliation.phase)}</b><span>{reconciliation.detail || relativeTime(reconciliation.updatedAt)}</span></div>}
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
