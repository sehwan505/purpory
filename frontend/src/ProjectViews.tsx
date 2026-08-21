import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent as ReactPointerEvent } from "react";
import type { app, graph, memory, prepare, project, reconcile } from "../wailsjs/go/models";
import { Dropdown } from "./Dropdown";

export type Page = "overview" | "workspace" | "reconcile" | "graph" | "projects" | "settings";
type GraphPosition = { x: number; y: number; vx: number; vy: number };

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
    graph: "M6 5a2 2 0 1 0 0 .01M18 7a2 2 0 1 0 0 .01M8 18a2 2 0 1 0 0 .01M16 16a2 2 0 1 0 0 .01M7.7 6.1l8.6.8M7 6.8l1 9.3m2-1 6-6",
    projects: "M4 5h16v14H4zM8 9h8M8 13h5",
    settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 3v2m0 14v2M3 12h2m14 0h2M5.6 5.6 7 7m10 10 1.4 1.4M18.4 5.6 17 7M7 17l-1.4 1.4",
  };
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d={paths[page]} /></svg>;
}

export function GraphView({ nodes, edges, matches = [], searchQuery, selectedID, emptyMessage, onSelect }: {
  nodes: graph.Node[];
  edges: graph.Edge[];
  matches?: app.QueryMatch[];
  searchQuery?: string;
  selectedID?: string;
  emptyMessage?: string;
  onSelect: (node: graph.Node) => void;
}) {
  const [filterQuery, setFilterQuery] = useState("");
  const [kind, setKind] = useState("");
  const [focused, setFocused] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [simulationVersion, setSimulationVersion] = useState(0);
  const [layout, setLayout] = useState<{ key: string; positions: Map<string, GraphPosition> }>({ key: "", positions: new Map() });
  const positionsRef = useRef(new Map<string, GraphPosition>());
  const dragRef = useRef<string | undefined>(undefined);
  const svgRef = useRef<SVGSVGElement>(null);
  const kinds = useMemo(() => [...new Set(nodes.map(node => node.kind))].sort(), [nodes]);
  const neighborIDs = useMemo(() => {
    const result = new Set(selectedID ? [selectedID] : []);
    if (selectedID) for (const edge of edges) {
      if (edge.sourceId === selectedID) result.add(edge.targetId);
      if (edge.targetId === selectedID) result.add(edge.sourceId);
    }
    return result;
  }, [edges, selectedID]);
  const normalizedQuery = filterQuery.trim().toLowerCase();
  const visibleNodes = nodes.filter(node => (!kind || node.kind === kind) && (!normalizedQuery || `${node.label} ${node.content}`.toLowerCase().includes(normalizedQuery)) && (!focused || !selectedID || neighborIDs.has(node.id))).slice(0, 36);
  const visibleIDs = new Set(visibleNodes.map(node => node.id));
  const visibleMatches = matches.filter(match => visibleIDs.has(match.node.id)).slice(0, 10);
  const queryMode = Boolean(searchQuery?.trim() && visibleMatches.length > 0);
  const centerID = !queryMode ? visibleNodes.some(node => node.id === selectedID) ? selectedID : visibleNodes[0]?.id : undefined;
  const adjacent = new Map<string, string[]>();
  for (const edge of edges) {
    adjacent.set(edge.sourceId, [...(adjacent.get(edge.sourceId) ?? []), edge.targetId]);
    adjacent.set(edge.targetId, [...(adjacent.get(edge.targetId) ?? []), edge.sourceId]);
  }
  const roots = queryMode ? visibleMatches.map(match => match.node.id) : centerID ? [centerID] : [];
  const distance = new Map<string, number>(roots.map(id => [id, 0]));
  let frontier = [...roots];
  while (frontier.length > 0) {
    const next: string[] = [];
    for (const id of frontier) for (const neighbor of adjacent.get(id) ?? []) if (!distance.has(neighbor)) {
      distance.set(neighbor, (distance.get(id) ?? 0) + 1);
      next.push(neighbor);
    }
    frontier = next;
  }
  const rootIDs = new Set(roots);
  const ordered = visibleNodes.filter(node => !rootIDs.has(node.id)).sort((left, right) => (distance.get(left.id) ?? 99) - (distance.get(right.id) ?? 99) || left.label.localeCompare(right.label));
  const initialPositions = new Map<string, GraphPosition>();
  const placeRing = (values: graph.Node[], radius: number, offset = 0) => values.forEach((node, index) => initialPositions.set(node.id, {
    x: 240 + Math.cos((index + offset) / values.length * Math.PI * 2 - Math.PI / 2) * radius,
    y: 175 + Math.sin((index + offset) / values.length * Math.PI * 2 - Math.PI / 2) * radius,
    vx: 0,
    vy: 0,
  }));
  if (queryMode) {
    placeRing(visibleMatches.map(match => match.node), 68);
    const split = Math.ceil(ordered.length / 2);
    placeRing(ordered.slice(0, split), 112, .5);
    placeRing(ordered.slice(split), 154);
  } else {
    if (centerID) initialPositions.set(centerID, { x: 240, y: 175, vx: 0, vy: 0 });
    let placed = 0;
    [10, 12, 13].forEach((capacity, ring) => {
      const values = ordered.slice(placed, placed + capacity);
      placeRing(values, 58 + ring * 46, ring / 2);
      placed += values.length;
    });
  }
  const visibleEdges = edges.filter(edge => visibleIDs.has(edge.sourceId) && visibleIDs.has(edge.targetId)).slice(0, 120);
  const layoutKey = [queryMode ? searchQuery : "", roots.join(","), visibleNodes.map(node => node.id).join(","), visibleEdges.map(edge => `${edge.sourceId}>${edge.targetId}`).join(",")].join("|");

  useEffect(() => {
    const points = new Map<string, GraphPosition>();
    for (const node of visibleNodes) {
      const point = positionsRef.current.get(node.id) ?? initialPositions.get(node.id) ?? { x: 240, y: 175, vx: 0, vy: 0 };
      points.set(node.id, { ...point, vx: 0, vy: 0 });
    }
    positionsRef.current = points;
    const semanticDistance = new Map(visibleMatches.map(match => {
      const score = match.signals.find(signal => signal.kind === "semantic")?.score ?? 0;
      return [match.node.id, 102 - Math.max(0, score) * 46] as const;
    }));
    let frame = 0;
    let tick = 0;
    const simulate = () => {
      const values = [...points.entries()];
      for (let leftIndex = 0; leftIndex < values.length; leftIndex++) for (let rightIndex = leftIndex + 1; rightIndex < values.length; rightIndex++) {
        const left = values[leftIndex][1];
        const right = values[rightIndex][1];
        let dx = right.x - left.x;
        let dy = right.y - left.y;
        if (dx === 0 && dy === 0) dx = .01 * (rightIndex + 1);
        const squared = Math.max(64, dx * dx + dy * dy);
        const length = Math.sqrt(squared);
        const force = 190 / squared;
        left.vx -= dx / length * force;
        left.vy -= dy / length * force;
        right.vx += dx / length * force;
        right.vy += dy / length * force;
      }
      for (const edge of visibleEdges) {
        const source = points.get(edge.sourceId);
        const target = points.get(edge.targetId);
        if (!source || !target) continue;
        const dx = target.x - source.x;
        const dy = target.y - source.y;
        const length = Math.max(1, Math.hypot(dx, dy));
        const force = (length - 62) * .018;
        source.vx += dx / length * force;
        source.vy += dy / length * force;
        target.vx -= dx / length * force;
        target.vy -= dy / length * force;
      }
      for (const [id, point] of points) {
        const targetDistance = queryMode ? semanticDistance.get(id) : id === centerID ? 0 : undefined;
        if (targetDistance !== undefined) {
          const dx = 240 - point.x;
          const dy = 175 - point.y;
          const length = Math.max(1, Math.hypot(dx, dy));
          const force = (length - targetDistance) * .025;
          point.vx += dx / length * force;
          point.vy += dy / length * force;
        }
        point.vx += (240 - point.x) * .0015;
        point.vy += (175 - point.y) * .0015;
        if (dragRef.current === id) {
          point.vx = 0;
          point.vy = 0;
          continue;
        }
        point.vx *= .86;
        point.vy *= .86;
        point.x = Math.min(462, Math.max(18, point.x + point.vx));
        point.y = Math.min(332, Math.max(18, point.y + point.vy));
      }
      setLayout({ key: layoutKey, positions: new Map([...points].map(([id, point]) => [id, { ...point }])) });
      if (++tick < 240) frame = window.requestAnimationFrame(simulate);
    };
    simulate();
    return () => window.cancelAnimationFrame(frame);
  }, [layoutKey, simulationVersion]);

  const positions = layout.key === layoutKey ? layout.positions : initialPositions;
  function pointerPosition(event: ReactPointerEvent<SVGGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM()?.inverse());
  }
  function dragNode(nodeID: string, event: ReactPointerEvent<SVGGElement>) {
    if (dragRef.current !== nodeID) return;
    const pointer = pointerPosition(event);
    const point = positionsRef.current.get(nodeID);
    if (!pointer || !point) return;
    point.x = Math.min(462, Math.max(18, pointer.x));
    point.y = Math.min(332, Math.max(18, pointer.y));
    point.vx = 0;
    point.vy = 0;
    setLayout({ key: layoutKey, positions: new Map([...positionsRef.current].map(([id, value]) => [id, { ...value }])) });
  }

  if (nodes.length === 0) return <div className="graphEmpty">{emptyMessage ?? "표시할 구조가 없습니다. 먼저 프로젝트를 업데이트해 주세요."}</div>;

  const width = 480 / zoom;
  const height = 350 / zoom;
  return <div className="graphCanvas">
    <div className="graphControls">
      <label><span className="srOnly">현재 그래프 필터</span><input value={filterQuery} onChange={event => setFilterQuery(event.target.value)} placeholder="현재 그래프 필터" /></label>
      <Dropdown value={kind} onChange={setKind} ariaLabel="노드 종류" options={[{ value: "", label: "모든 종류" }, ...kinds.map(value => ({ value, label: value }))]} />
      <button type="button" className={focused ? "active" : ""} disabled={!selectedID} onClick={() => setFocused(value => !value)}>이웃만</button>
      <button type="button" aria-label="축소" disabled={zoom <= .75} onClick={() => setZoom(value => Math.max(.75, value - .25))}>−</button>
      <button type="button" aria-label="화면 맞춤 및 노드 재배치" onClick={() => { positionsRef.current = new Map(); setZoom(1); setSimulationVersion(value => value + 1); }}>맞춤</button>
      <button type="button" aria-label="확대" disabled={zoom >= 2} onClick={() => setZoom(value => Math.min(2, value + .25))}>+</button>
    </div>
    <div className="graphStats">{visibleNodes.length}/{nodes.length} nodes · {visibleEdges.length}/{edges.length} relations</div>
    <div className="graphLegend" aria-label="노드 색상 범례">
      {(["intent", "material", "knowledge"] as const).map(kind => <span key={kind}><i aria-hidden="true" style={{ background: nodeColor(kind) }} />{kind}</span>)}
      {queryMode && <span><i className="semanticLine" aria-hidden="true" />검색 연관</span>}
    </div>
    {visibleNodes.length === 0 && <div className="graphFilteredEmpty">필터에 맞는 노드가 없습니다.</div>}
    <svg ref={svgRef} viewBox={`${(480 - width) / 2} ${(350 - height) / 2} ${width} ${height}`} role="img" aria-label="Intent, Material, Knowledge 관계 그래프">
      <defs>
        <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M 20 0 L 0 0 0 20" fill="none" stroke="#dfe3da" strokeWidth=".5" /></pattern>
        <marker id="graphArrow" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0,0 L0,6 L6,3 z" fill="#8e998a" /></marker>
      </defs>
      <rect width="480" height="350" fill="url(#grid)" />
      {visibleEdges.map((edge, index) => {
        const source = positions.get(edge.sourceId)!;
        const target = positions.get(edge.targetId)!;
        const emphasized = edge.sourceId === selectedID || edge.targetId === selectedID;
        return <g className={`graphEdge${emphasized ? " emphasized" : ""}`} key={`${edge.sourceId}-${edge.targetId}-${index}`}>
          <line x1={source.x} y1={source.y} x2={target.x} y2={target.y} markerEnd="url(#graphArrow)"><title>{edge.relation}</title></line>
          {(emphasized || visibleEdges.length <= 14) && <text x={(source.x + target.x) / 2} y={(source.y + target.y) / 2 - 3}>{edge.relation.replaceAll("_", " ")}</text>}
        </g>;
      })}
      {queryMode && visibleMatches.map(match => {
        const target = positions.get(match.node.id)!;
        const label = match.signals.map(signalLabel).join(" + ");
        return <g className="queryMatch" key={`query-${match.node.id}`}>
          <line x1="240" y1="175" x2={target.x} y2={target.y}><title>{label}</title></line>
          <text x={(240 + target.x) / 2} y={(175 + target.y) / 2 - 3}>{label}</text>
        </g>;
      })}
      {queryMode && <g className="queryNode" aria-label={`검색 질문 ${searchQuery}`}>
        <rect x="198" y="158" width="84" height="34" rx="9" />
        <text x="240" y="172">SEARCH</text>
        <text x="240" y="184">{searchQuery!.trim().slice(0, 22)}</text>
        <title>{searchQuery}</title>
      </g>}
      {visibleNodes.map(node => {
        const position = positions.get(node.id)!;
        const selected = node.id === selectedID;
        const color = nodeColor(node.kind, node.state);
        return <g className={`graphNode${dragRef.current === node.id ? " dragging" : ""}`} key={node.id} role="button" tabIndex={0} aria-label={`${node.kind} ${node.label}`} onPointerDown={event => { dragRef.current = node.id; event.currentTarget.setPointerCapture(event.pointerId); setSimulationVersion(value => value + 1); }} onPointerMove={event => dragNode(node.id, event)} onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); dragRef.current = undefined; setSimulationVersion(value => value + 1); }} onPointerCancel={() => { dragRef.current = undefined; setSimulationVersion(value => value + 1); }} onClick={() => onSelect(node)} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") onSelect(node); }}>
          <circle cx={position.x} cy={position.y} r={selected ? 7 : 5} fill={color} stroke={selected ? "#1a2019" : "#fff"} strokeWidth={selected ? 2 : 1.5} />
          <circle cx={position.x} cy={position.y} r={node.content ? 10 : 8} fill="none" stroke={color} strokeOpacity={node.content ? ".28" : ".14"} />
          <text x={position.x + 10} y={position.y + 3} fill="#4e584b" fontSize="7">{node.label.slice(0, 26)}</text>
          <title>{node.label} · {node.kind}{node.subkind ? `/${node.subkind}` : ""}{node.content ? " · 내용 있음" : ""}{node.state === "missing" ? " · missing" : ""}</title>
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
  const current = explanation?.graph?.node ?? node;
  const connections = explanation?.graph?.connections ?? [];
  return <div className="nodeDetails">
    <div className="nodeBadges"><span>{current.kind}{current.subkind ? `/${current.subkind}` : ""}</span><span>{current.owner}</span><span className={current.state === "missing" ? "missing" : ""}>{current.state}</span></div>
    <h3>{current.label}</h3>
    <dl className="nodeFacts">
      {current.path && <><dt>Path</dt><dd>{current.path}</dd></>}
      <dt>Reference</dt><dd>{current.ref}</dd>
      {current.provenance && <><dt>Provenance</dt><dd>{current.provenance}</dd></>}
      {current.materialUri && <><dt>Source</dt><dd>{current.materialUri}{current.locator ? `#${current.locator}` : ""}</dd></>}
    </dl>
    <section className="nodeContent"><strong>실제 데이터</strong>{current.content ? <pre>{current.content}</pre> : <p>{current.kind === "material" ? "Material은 원문을 복제하지 않습니다. 아래에 연결된 Knowledge를 선택하면 추출된 내용을 확인할 수 있습니다." : "이 노드에는 저장된 본문이 없습니다."}</p>}</section>
    {durable && <div className="memoryActions"><button type="button" disabled={busy} onClick={() => onEdit?.(durable)}>편집</button><button type="button" className="secondary" disabled={busy} onClick={() => onConfirm?.(durable.key)}>유효함</button><button type="button" className="danger" disabled={busy} onClick={() => onDelete?.(durable.key)}>삭제</button></div>}
    <strong className="connectionTitle">연결 {connections.length}</strong>
    <div className="connections">{connections.length === 0 ? <p className="empty">직접 연결된 노드가 없습니다.</p> : connections.map(connection => <button type="button" key={`${connection.direction}-${connection.relation}-${connection.node.id}`} onClick={() => onSelect(connection.node)}><span>{connection.direction === "out" ? "→" : "←"} {connection.relation}</span><strong>{connection.node.label}</strong><small>{connection.node.content || [connection.node.materialUri, connection.node.locator].filter(Boolean).join("#") || `${connection.node.kind}${connection.node.subkind ? `/${connection.node.subkind}` : ""}`}</small></button>)}</div>
  </div>;
}

function signalLabel(signal: app.QuerySignal) {
  return signal.score ? `${signal.kind} ${signal.score.toFixed(3)}` : signal.kind;
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
  const [selectedProjectID, setSelectedProjectID] = useState(currentID ?? "");
  const [search, setSearch] = useState("");
  useEffect(() => setSelectedProjectID(currentID ?? ""), [currentID]);
  const selectedProject = projects.find(item => item.id === selectedProjectID) ?? projects.find(item => item.id === currentID) ?? projects[0];
  const normalizedSearch = search.trim().toLowerCase();
  const matchesSearch = (value: project.Observation) => `${value.resource.label} ${value.resource.identity} ${value.resource.views?.map(view => view.root).join(" ") ?? ""}`.toLowerCase().includes(normalizedSearch);
  const assignedTo = (projectID: string) => observations.filter(value => (value.projectIds ?? []).includes(projectID));
  const visibleProjects = projects.filter(item => !normalizedSearch || item.name.toLowerCase().includes(normalizedSearch) || assignedTo(item.id).some(matchesSearch));
  const assigned = selectedProject ? assignedTo(selectedProject.id) : [];
  const visibleAssigned = normalizedSearch ? assigned.filter(matchesSearch) : assigned;
  const available = selectedProject ? observations.filter(value => !(value.projectIds ?? []).includes(selectedProject.id)) : [];
  const unassigned = observations.filter(value => (value.projectIds ?? []).length === 0);
  const visibleUnassigned = normalizedSearch ? unassigned.filter(matchesSearch) : unassigned;

  return <section className="projectManagement">
    <section className="panel createProjectPanel">
      <div><p className="eyebrow">GLOBAL PROJECTS</p><h2>Project 만들기</h2><p>Project는 Repository와 독립적인 작업 범위입니다. 만든 뒤 필요한 Repository를 연결하세요.</p></div>
      <form onSubmit={onCreate}><label htmlFor="project-name">Project 이름</label><div><input id="project-name" name="name" maxLength={120} placeholder="예: Purpory Desktop" required /><button disabled={busy}>만들기</button></div></form>
    </section>
    <section className="panel assignmentPanel">
      <div className="sectionTitle"><div><p className="eyebrow">PROJECT STRUCTURE</p><h2>Project와 Repository</h2></div><span>{projects.length} / {observations.length}</span></div>
      <p className="panelIntro">Project를 선택하면 그 안에 속한 Repository를 확인하고 연결을 관리할 수 있습니다.</p>
      <label className="projectSearch"><span className="srOnly">Project 또는 Repository 검색</span><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="Project 또는 Repository 검색" /></label>
      <div className="assignmentLayout">
        <div className="projectIndex" role="navigation" aria-label="Project 목록">
          <div className="projectIndexTitle"><strong>Projects</strong><span>{visibleProjects.length}</span></div>
          <div className="projectIndexList">{visibleProjects.length === 0 ? <p className="empty">검색 결과가 없습니다.</p> : visibleProjects.map(item => {
            const repositoryCount = assignedTo(item.id).length;
            return <button type="button" className={item.id === selectedProject?.id ? "selected" : ""} aria-current={item.id === selectedProject?.id ? "true" : undefined} key={item.id} onClick={() => setSelectedProjectID(item.id)} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); const resourceID = event.dataTransfer.getData("text/plain"); if (resourceID) onAssign(item.id, resourceID); }}>
              <span className="projectMark" aria-hidden="true">{item.name.slice(0, 1).toUpperCase() || "P"}</span>
              <span><strong>{item.name}</strong><small>{repositoryCount} Repositories</small></span>
              {item.id === currentID && <b>현재</b>}
            </button>;
          })}</div>
        </div>

        <div className="projectRepositoryDetail">{selectedProject ? <>
          <header className="projectDetailHeader">
            <div><p className="projectBreadcrumb">PROJECT <span aria-hidden="true">/</span> {selectedProject.name}</p><h3>{selectedProject.name}</h3><p>{assigned.length}개 Repository가 이 Project에 속해 있습니다.</p></div>
            {selectedProject.id !== currentID && <button type="button" className="textButton" disabled={busy} onClick={() => onSelect(selectedProject.id)}>이 Project 열기 →</button>}
          </header>
          {available.length > 0 && <form className="projectAddRepository" onSubmit={event => { event.preventDefault(); const resourceID = String(new FormData(event.currentTarget).get("resource") ?? ""); if (resourceID) onAssign(selectedProject.id, resourceID); }}>
            <label htmlFor="project-resource">Repository 연결</label>
            <div><Dropdown id="project-resource" name="resource" defaultValue={available[0]?.resource.id} options={available.map(value => ({ value: value.resource.id, label: `${value.resource.label} · ${(value.projectIds ?? []).length === 0 ? "미소속" : `${value.projectIds.length}개 Project`}` }))} /><button disabled={busy}>연결</button></div>
          </form>}
          <section className="repositoryGroup" aria-label={`${selectedProject.name}의 Repository`}>
            <div className="repositoryGroupTitle"><strong>Repositories</strong><span>{visibleAssigned.length}</span></div>
            <div className="repositoryRows">{visibleAssigned.length === 0 ? <p className="empty">{assigned.length === 0 ? "아직 연결된 Repository가 없습니다." : "검색 결과가 없습니다."}</p> : visibleAssigned.map(value => <article key={value.resource.id} draggable onDragStart={event => event.dataTransfer.setData("text/plain", value.resource.id)}>
              <span className="repositoryBranch" aria-hidden="true" />
              <div><strong>{value.resource.label}</strong><small>{value.resource.views?.[0]?.root ?? value.resource.identity}</small><span>{value.resource.provider} · {value.resource.views?.length ?? 0} Views · {relativeTime(value.observedAt)}</span></div>
              <button type="button" aria-label={`${selectedProject.name}에서 ${value.resource.label} 연결 해제`} disabled={busy} onClick={() => onUnassign(selectedProject.id, value.resource.id)}>해제</button>
            </article>)}</div>
          </section>
        </> : <p className="empty">먼저 Project를 만들어 주세요.</p>}</div>
      </div>

      <section className="repositoryInbox">
        <div className="repositoryGroupTitle"><div><strong>미소속 Repository</strong><small>Hook에서 발견됐지만 아직 어느 Project에도 연결되지 않았습니다.</small></div><span>{visibleUnassigned.length}</span></div>
        <div className="repositoryInboxRows">{visibleUnassigned.length === 0 ? <p className="empty">{unassigned.length === 0 ? "모든 Repository가 Project에 연결되어 있습니다." : "검색 결과가 없습니다."}</p> : visibleUnassigned.map(value => <article key={value.resource.id} draggable onDragStart={event => event.dataTransfer.setData("text/plain", value.resource.id)}>
          <div><strong>{value.resource.label}</strong><small>{value.resource.views?.[0]?.root ?? value.resource.identity}</small></div>
          {projects.length > 0 && <form onSubmit={event => { event.preventDefault(); const projectID = String(new FormData(event.currentTarget).get("project") ?? ""); if (projectID) onAssign(projectID, value.resource.id); }}><Dropdown name="project" ariaLabel={`${value.resource.label}을 추가할 Project`} defaultValue={selectedProject?.id ?? projects[0]?.id} options={projects.map(item => ({ value: item.id, label: item.name }))} /><button disabled={busy}>연결</button></form>}
        </article>)}</div>
      </section>
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
      <details className="queueRunMeta"><summary>Session 정보 · 등록 {relativeTime(run.queuedAt)} · 갱신 {relativeTime(run.updatedAt)}</summary><code>{run.sessionId}</code></details>
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
          <form className="inlineForm" onSubmit={event => onResolveRequest(event, request.id)}><Dropdown name="memory" ariaLabel="해결 근거" defaultValue="" required options={[{ value: "", label: "해결 근거 선택", disabled: true }, ...memories.map(item => ({ value: item.key, label: item.key }))]} /><button disabled={busy || memories.length === 0}>해결</button></form>
        </article>)}</div>}
      </section>
      <section className="operationCard"><div className="cardTitle"><div><p className="eyebrow">NEEDS REVIEW</p><h3>메모리 검토</h3></div><strong>{reviews.length}</strong></div>
        {reviews.length === 0 ? <p className="empty">검토할 메모리가 없습니다.</p> : <div className="operationList">{reviews.map(review => <article key={review.id}>
          <div className="itemMeta"><span>{review.sourceType} · {relativeTime(review.createdAt)}</span><span>#{review.id}</span></div><strong>{review.key}</strong><p>{review.reason}</p><small>{review.sourceId}</small>
          <div className="itemActions"><button className="secondary" disabled={busy} onClick={() => onResolveReview(review.id)}>현재 내용 유지</button><button className="textButton" onClick={() => onOpenMemory(review.key)}>Graph에서 확인</button></div>
          <details className="reviewChange"><summary>내용 수정 후 해결</summary><form onSubmit={event => onChangeReview(event, review)}><div className="dropdownField"><label htmlFor={`review-kind-${review.id}`}>종류</label><Dropdown id={`review-kind-${review.id}`} name="kind" defaultValue={memories.find(item => item.key === review.key)?.kind || "note"} options={[{ value: "note", label: "지식" }, { value: "decision", label: "결정" }, { value: "reference", label: "참조" }]} /></div><label>새 내용<textarea name="value" defaultValue={memories.find(item => item.key === review.key)?.value || ""} required /></label><button disabled={busy}>수정 적용</button></form></details>
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
        {decisions.length === 0 ? <p className="empty">판단 기록이 없습니다.</p> : <div className="auditList">{decisions.map(decision => <details key={decision.id}><summary><span className={`action ${decision.finalAction}`}>{decision.finalAction}</span><strong>{decision.inputText || `입력 해시 ${decision.inputHash.slice(0, 12)}`}</strong><small>{relativeTime(decision.createdAt)}</small></summary><div className="auditBody"><p>{decision.proposal.reasonCode} · {(decision.hints?.nodes ?? []).map(item => item.path || item.id).join(", ") || "힌트 없음"}</p><form className="feedbackForm" onSubmit={event => onFeedback(event, decision.id)}><div className="dropdownField"><label htmlFor={`decision-verdict-${decision.id}`}>판단 평가</label><Dropdown id={`decision-verdict-${decision.id}`} name="verdict" defaultValue={decision.feedback?.verdict || "correct"} options={[{ value: "correct", label: "맞음" }, { value: "incorrect", label: "수정 필요" }]} /></div><div className="dropdownField"><label htmlFor={`decision-action-${decision.id}`}>기대한 동작</label><Dropdown id={`decision-action-${decision.id}`} name="expectedAction" defaultValue={decision.feedback?.expectedAction || ""} options={[{ value: "", label: "해당 없음" }, { value: "skip", label: "skip" }, { value: "retrieve", label: "retrieve" }, { value: "ask", label: "ask" }]} /></div><label>기대한 메모리 키<input name="expectedKeys" defaultValue={decision.feedback?.expectedKeys?.join(", ") || ""} /></label><label>메모<input name="note" defaultValue={decision.feedback?.note || ""} /></label><button disabled={busy}>피드백 저장</button></form></div></details>)}</div>}
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
