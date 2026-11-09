import {
  useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent, type ReactNode,
} from 'react';
import {
  CHAT_MATCH_LIMIT, DRAG_CLICK_TOLERANCE, KIND_LABEL, LOGIC_LABEL, NODE_LABEL_MAX, POLICY_SEARCH_LIMIT, RELATION_LABEL, ZOOM_MAX, ZOOM_MIN, ZOOM_STEP,
} from '../config/graph';
import {
  conditionTrees, countConditions, policyNeighbors, searchPolicies,
  type ConditionTree, type GraphNode,
} from '../graph/graphModel';
import {
  INITIAL_VIEW, MAP_HEIGHT, MAP_WIDTH, layoutNeighbors, shortLabel, zoomAt, zoomCenter, type MapView, type PlacedNeighbor,
} from '../graph/graphLayout';
import { loadGraphDataset, type GraphDataset } from '../graph/loadGraph';
import { policiesForDocuments, policiesForQuestion } from '../graph/relatedPolicies';
import { mapHref } from '../lib/route';
import type { RecentPolicies } from '../lib/recentPolicies';
import { safeHttpUrl } from '../lib/validate';
import { Icon } from './Icon';

type LoadState = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; data: GraphDataset };

/**
 * 정책 관계 지도: 정책 하나를 가운데 두고 Neo4j에 저장된 기관·지역·대상·근거 문서·자격 조건 연결을 보여 준다.
 * 실제 팀 데이터(data/releases)를 그대로 읽는다. 그래프 연결은 정책 구조이지 자격 확정이 아니다.
 */
export default function PolicyMap({ policyId, recent }: {
  policyId: string | null;
  /** 상담 화면에서 가장 최근 답변으로 나온 정책. 주소에 정책이 없으면 이 중 첫 정책을 연다 */
  recent: RecentPolicies | null;
}) {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    loadGraphDataset()
      .then((data) => { if (alive) setState({ kind: 'ready', data }); })
      .catch((err: unknown) => { if (alive) setState({ kind: 'error', message: err instanceof Error ? err.message : '그래프 데이터를 읽지 못했어요.' }); });
    return () => { alive = false; };
  }, []);

  // 방금 상담과 이어지는 정책: 답변의 정책·출처 문서 → (없으면) 질문과 비슷한 정책 검색
  const fromChat = useMemo(() => (state.kind === 'ready' && recent ? chatPolicies(state.data, recent) : null), [state, recent]);
  const openId = policyId ?? fromChat?.policies[0]?.id ?? null;

  // 다른 정책으로 바뀌면 선택한 노드를 정책 자신으로 되돌린다
  useEffect(() => { setSelectedId(openId); }, [openId]);

  if (state.kind === 'loading') {
    return <MapShell><div className="map-msg" role="status">정책 관계 데이터를 불러오고 있어요…</div></MapShell>;
  }
  if (state.kind === 'error') {
    return <MapShell><div className="notice bad"><Icon name="warning" /><div><b>관계 지도를 열지 못했어요</b><p>{state.message}</p></div></div></MapShell>;
  }

  const { index } = state.data;
  const policy = openId ? index.nodes.get(openId) : undefined;
  const search = <PolicySearch data={state.data} query={query} onQuery={setQuery} currentId={policy?.id ?? null} />;
  return (
    <MapShell data={state.data}>
      {recent && fromChat && fromChat.policies.length > 0 && (
        <div className="from-chat" aria-label="상담과 이어지는 정책">
          <span className="from-chat-q">
            <Icon name="info" className="ic" />
            {fromChat.kind === 'answer'
              ? <>방금 상담한 “{recent.question}” 질문의 답변에 나온 정책</>
              : <>방금 상담한 “{recent.question}” 질문과 이름·분야가 비슷한 정책 <small>(질문 단어로 찾은 검색 결과예요. 답변 근거나 자격 판정이 아니에요)</small></>}
          </span>
          {fromChat.policies.map((p) => (
            <a key={p.id} href={mapHref(p.id)} aria-current={p.id === openId ? 'page' : undefined}>{p.label}</a>
          ))}
        </div>
      )}
      {/* 지도를 맨 위에 크게, 정책 찾기·상세·자격 조건은 그 아래에 둔다 */}
      {policy && policy.kind === 'policy'
        ? <PolicyGraph data={state.data} policy={policy} selectedId={selectedId ?? policy.id} onSelect={setSelectedId} search={search} />
        : (
          <>
            <div className="map-empty">
              <Icon name="search" />
              <b>{policyId ? '이 정책을 데이터에서 찾지 못했어요' : '아래에서 정책을 골라 주세요'}</b>
              <span>정책을 고르면 기관·지역·대상·근거 문서·자격 조건이 어떻게 연결돼 있는지 보여 드려요.</span>
            </div>
            <div className="map-below">{search}</div>
          </>
        )}
    </MapShell>
  );
}

/** 상담 맥락 → 지도에서 열 정책들. answer: 답변의 정책·출처(근거 있음), search: 질문 단어 검색 */
function chatPolicies(data: GraphDataset, recent: RecentPolicies): { kind: 'answer' | 'search'; policies: GraphNode[] } {
  const { index } = data;
  const answered = recent.policies.map((p) => index.nodes.get(p.id)).filter((n): n is GraphNode => n?.kind === 'policy');
  const fromDocs = policiesForDocuments(index, recent.documentIds).filter((p) => !answered.includes(p));
  if (answered.length + fromDocs.length > 0) return { kind: 'answer', policies: [...answered, ...fromDocs] };
  return { kind: 'search', policies: policiesForQuestion(index, recent.question, CHAT_MATCH_LIMIT) };
}

function MapShell({ data, children }: { data?: GraphDataset; children: ReactNode }) {
  const counted = data ? { nodes: data.index.nodes.size, edges: data.index.edgeCount } : null;
  // manifest 수와 실제로 읽은 수가 다르면 데이터가 덜 받아졌거나 섞인 것이라 알린다
  const mismatch = data?.expected && counted && (data.expected.nodes !== counted.nodes || data.expected.edges !== counted.edges);
  return (
    <section className="board map" id="board">
      <div className="wrap">
        <div className="board-h">
          <h2>정책 <span>관계 지도</span></h2>
          <p>Graph DB(Neo4j)에 저장된 정책 연결 구조예요</p>
        </div>
        {data && counted && (
          <p className="map-meta">
            <span className="badge b-muted">데이터 {data.version}{data.status ? ` · ${data.status}` : ''}</span>
            <span>정책 {data.index.policies.length.toLocaleString('ko-KR')}개 · 노드 {counted.nodes.toLocaleString('ko-KR')}개 · 관계 {counted.edges.toLocaleString('ko-KR')}개</span>
            {mismatch && <span className="badge b-warn">manifest 건수와 달라요</span>}
          </p>
        )}
        {children}
        <p className="map-note">관계가 연결돼 있어도 자격이 확정된 건 아니에요. 최종 자격과 신청 가능 여부는 공식 원문에서 확인해 주세요.</p>
      </div>
    </section>
  );
}

function PolicySearch({ data, query, onQuery, currentId }: { data: GraphDataset; query: string; onQuery: (v: string) => void; currentId: string | null }) {
  const results = useMemo(() => searchPolicies(data.index, query, POLICY_SEARCH_LIMIT), [data, query]);
  return (
    <aside className="map-side" aria-label="정책 찾기">
      <input className="map-search" value={query} onChange={(e) => onQuery(e.target.value)} placeholder="정책 이름·지역·분야로 찾기" aria-label="정책 찾기" />
      <ul className="map-list">
        {results.map((p) => (
          <li key={p.id}>
            <a href={mapHref(p.id)} aria-current={p.id === currentId ? 'page' : undefined}>
              <b>{p.label}</b>
              <small>{[p.data.category, p.data.region].filter((v) => typeof v === 'string').join(' · ')}</small>
            </a>
          </li>
        ))}
        {results.length === 0 && <li className="map-none">찾는 정책이 없어요</li>}
      </ul>
      {results.length === POLICY_SEARCH_LIMIT && <p className="map-more">결과가 많아 {POLICY_SEARCH_LIMIT}개까지만 보여 드려요. 더 좁혀 찾아보세요.</p>}
    </aside>
  );
}

function PolicyGraph({ data, policy, selectedId, onSelect, search }: {
  data: GraphDataset; policy: GraphNode; selectedId: string; onSelect: (id: string) => void; search: ReactNode;
}) {
  const { index } = data;
  const neighbors = useMemo(() => policyNeighbors(index, policy.id), [index, policy.id]);
  const trees = useMemo(() => conditionTrees(index, policy.id), [index, policy.id]);
  const { center, placed } = useMemo(() => layoutNeighbors(neighbors), [neighbors]);
  const selected = selectedId === policy.id ? null : placed.find((n) => n.node.id === selectedId) ?? null;
  // 범례: 가운데 정책 + 둘레에 실제로 있는 종류 (정책끼리 연결돼도 '정책'은 한 번만)
  const kinds = [...new Set(['policy' as const, ...placed.map((n) => n.node.kind)])];

  return (
    <div className="map-main">
      <div className="map-head">
        <b>{policy.label}</b>
        <small>{[policy.data.agency, policy.data.category, policy.data.region].filter((v) => typeof v === 'string').join(' · ')}</small>
        <span className="map-count">연결 {neighbors.length}개 · 자격 조건 {countConditions(trees)}개</span>
      </div>

      <div className="map-stage">
      <ZoomCanvas resetKey={policy.id} label={`${policy.label} 관계 지도`}>
          {placed.map((n) => (
            <line key={`l-${n.node.id}-${n.edge.relation}`} x1={center.x} y1={center.y} x2={n.x} y2={n.y}
              className={'gl' + (n.node.id === selectedId ? ' on' : '')} />
          ))}
          {placed.map((n) => <EdgeLabel key={`e-${n.node.id}-${n.edge.relation}`} n={n} cx={center.x} cy={center.y} />)}
          <MapNode x={center.x} y={center.y} r={46} kind="policy" label={policy.label} title={policy.label}
            active={selectedId === policy.id} onSelect={() => onSelect(policy.id)} center />
          {placed.map((n) => (
            <MapNode key={`n-${n.node.id}-${n.edge.relation}`} x={n.x} y={n.y} r={30} kind={n.node.kind}
              label={n.node.kind === 'group' ? KIND_LABEL.group : n.node.label}
              title={n.node.kind === 'group' ? nodeTitle(n.node) : `${RELATION_LABEL[n.edge.relation] ?? n.edge.relation}: ${n.node.label}`}
              active={n.node.id === selectedId} onSelect={() => onSelect(n.node.id)} />
          ))}
      </ZoomCanvas>
      <ul className="map-legend" aria-label="범례">
        {kinds.map((k) => <li key={k}><i className={`k-${k}`} />{KIND_LABEL[k]}</li>)}
      </ul>
      </div>

      <div className="map-below">
        {search}
        <div className="map-detail">
          {selected ? <NodeDetail n={selected} data={data} /> : <PolicyDetail policy={policy} />}
        </div>
        <div className="map-tree">
          <h3><Icon name="check" className="ic" />자격 조건 구조</h3>
          {trees.length === 0
            ? <p className="map-dim">연결된 자격 조건 묶음이 없어요.</p>
            : trees.map((t) => <TreeView key={t.id} tree={t} />)}
        </div>
      </div>
    </div>
  );
}

/**
 * 확대·이동이 되는 지도 틀.
 * - 버튼: 확대(+)·축소(−)·원래대로, 전체 화면 (발표용)
 * - 끌기: 지도 이동. 끈 뒤에는 노드 클릭으로 처리하지 않는다
 * - Ctrl(⌘)+휠: 마우스 위치를 기준으로 확대·축소. 그냥 휠은 페이지 스크롤로 남겨 둔다
 * - 키보드: 지도에 초점이 있을 때 + / − / 0
 */
function ZoomCanvas({ resetKey, label, children }: { resetKey: string; label: string; children: ReactNode }) {
  const [view, setView] = useState<MapView>(INITIAL_VIEW);
  const [fullscreen, setFullscreen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ id: number; sx: number; sy: number; vx: number; vy: number; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  // 끌 때 pointermove가 화면 그리기보다 자주 와서, 다음 프레임에 마지막 위치만 반영한다
  const frame = useRef(0);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  // 다른 정책을 열면 원래 보기로 돌아간다
  useEffect(() => { setView(INITIAL_VIEW); }, [resetKey]);

  /** 화면 좌표 → 지도 좌표 (viewBox 단위) */
  const toMap = (clientX: number, clientY: number) => {
    const r = svgRef.current!.getBoundingClientRect();
    return { x: ((clientX - r.left) / r.width) * MAP_WIDTH, y: ((clientY - r.top) / r.height) * MAP_HEIGHT };
  };
  const zoomBy = (factor: number) => setView((v) => zoomCenter(v, factor, ZOOM_MIN, ZOOM_MAX));

  // 휠은 React onWheel이 passive라 preventDefault가 안 돼서 직접 등록한다
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const point = toMap(e.clientX, e.clientY);
      setView((v) => zoomAt(v, e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP, point, ZOOM_MIN, ZOOM_MAX));
    };
    svg.addEventListener('wheel', onWheel, { passive: false });
    return () => svg.removeEventListener('wheel', onWheel);
  }, []);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === boxRef.current);
    document.addEventListener('fullscreenchange', onChange);
    return () => document.removeEventListener('fullscreenchange', onChange);
  }, []);

  const toggleFullscreen = () => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void boxRef.current?.requestFullscreen?.().catch(() => { /* 브라우저가 막으면 일반 보기로 둔다 */ });
  };

  const onPointerDown = (e: PointerEvent<SVGSVGElement>) => {
    if (e.button !== 0) return;
    // 지난번 끌기 뒤 클릭이 오지 않았으면 남은 표시를 지운다 (다음 정상 클릭이 무시되지 않게)
    suppressClick.current = false;
    drag.current = { id: e.pointerId, sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y, moved: false };
  };
  const onPointerMove = (e: PointerEvent<SVGSVGElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    const dx = e.clientX - d.sx, dy = e.clientY - d.sy;
    if (!d.moved && Math.hypot(dx, dy) < DRAG_CLICK_TOLERANCE) return;
    if (!d.moved) { d.moved = true; e.currentTarget.setPointerCapture(e.pointerId); }
    const r = e.currentTarget.getBoundingClientRect();
    const x = d.vx + (dx / r.width) * MAP_WIDTH, y = d.vy + (dy / r.height) * MAP_HEIGHT;
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => setView((v) => ({ ...v, x, y })));
  };
  const onPointerUp = () => {
    suppressClick.current = !!drag.current?.moved;
    drag.current = null;
  };
  // 끌기가 취소되면(창 전환 등) 뒤따르는 클릭이 없으므로 표시를 남기지 않는다
  const onPointerCancel = () => { suppressClick.current = false; drag.current = null; };
  // 끌기를 마친 직후의 클릭은 노드 선택으로 보지 않는다
  const onClickCapture = (e: MouseEvent<SVGSVGElement>) => {
    if (suppressClick.current) { e.stopPropagation(); suppressClick.current = false; }
  };
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return; // 노드에 초점이 있을 때 Enter·Space는 노드 선택에 쓴다
    if (e.key === '+' || e.key === '=') zoomBy(ZOOM_STEP);
    else if (e.key === '-') zoomBy(1 / ZOOM_STEP);
    else if (e.key === '0') setView(INITIAL_VIEW);
    else return;
    e.preventDefault();
  };

  return (
    <div ref={boxRef} className={'map-canvas' + (fullscreen ? ' full' : '')} tabIndex={0} onKeyDown={onKeyDown}
      aria-label={`${label}. 지도에 초점을 두고 + − 0 키로 확대·축소할 수 있어요`}>
      <div className="map-zoom" role="toolbar" aria-label="지도 확대·축소">
        <button type="button" onClick={() => zoomBy(ZOOM_STEP)} disabled={view.scale >= ZOOM_MAX} aria-label="확대">+</button>
        <button type="button" onClick={() => zoomBy(1 / ZOOM_STEP)} disabled={view.scale <= ZOOM_MIN} aria-label="축소">−</button>
        <button type="button" onClick={() => setView(INITIAL_VIEW)} aria-label="원래 크기로">{Math.round(view.scale * 100)}%</button>
        <button type="button" onClick={toggleFullscreen} aria-label={fullscreen ? '전체 화면 끝내기' : '전체 화면으로 보기'}>{fullscreen ? '닫기' : '전체'}</button>
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`} role="group" aria-label={label}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerCancel}
        onClickCapture={onClickCapture}>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.scale})`}>{children}</g>
      </svg>
      <p className="map-hint">끌어서 옮기고, Ctrl(⌘)+휠이나 + − 버튼으로 확대해요</p>
    </div>
  );
}

/** 조건 묶음 노드는 이름 대신 논리(모두 충족 등)를 보여 준다 */
function nodeTitle(node: GraphNode): string {
  return node.kind === 'group' ? `${KIND_LABEL.group} (${LOGIC_LABEL[node.label] ?? node.label})` : node.label;
}

function MapNode({ x, y, r, kind, label, title, active, onSelect, center }: {
  x: number; y: number; r: number; kind: string; label: string; title: string; active: boolean; onSelect: () => void; center?: boolean;
}) {
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); } };
  return (
    <g className={`gn k-${kind}${active ? ' on' : ''}${center ? ' center' : ''}`} transform={`translate(${x} ${y})`}
      role="button" tabIndex={0} aria-label={title} aria-pressed={active} onClick={onSelect} onKeyDown={onKey}>
      <title>{title}</title>
      <circle r={r} />
      <text y={center ? 4 : r + 16} textAnchor="middle">{shortLabel(label, center ? 9 : NODE_LABEL_MAX)}</text>
    </g>
  );
}

function EdgeLabel({ n, cx, cy }: { n: PlacedNeighbor; cx: number; cy: number }) {
  const label = RELATION_LABEL[n.edge.relation] ?? n.edge.relation;
  return <text className="ge" x={(cx + n.x) / 2} y={(cy + n.y) / 2} textAnchor="middle">{label}</text>;
}

function PolicyDetail({ policy }: { policy: GraphNode }) {
  const source = safeHttpUrl(typeof policy.data.source_url === 'string' ? policy.data.source_url : null);
  const rows: [string, unknown][] = [['지원 대상', policy.data.eligibility], ['지원 내용', policy.data.benefit], ['신청 기간', policy.data.application_period]];
  return (
    <div className="map-info">
      <span className="badge b-muted">정책</span>
      <b>{policy.label}</b>
      <dl>
        {rows.filter(([, v]) => typeof v === 'string' && v.trim()).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v as string}</dd></div>)}
      </dl>
      <p className="map-dim">데이터에 정리된 내용이에요. 지도의 원을 누르면 연결된 항목을 자세히 보여 드려요.</p>
      {source && <a className="btn btn-o" href={source} target="_blank" rel="noopener noreferrer">원문 보기 ↗</a>}
    </div>
  );
}

/** 종류와 관계 이름이 같으면(예: 소관 기관) 한 번만 쓴다 */
const relationBadge = (kind: string, relation: string) => (kind === relation ? kind : `${kind} · ${relation}`);

function NodeDetail({ n, data }: { n: PlacedNeighbor; data: GraphDataset }) {
  const { node, edge } = n;
  const evidence = edge.evidenceDocumentId ? data.index.nodes.get(edge.evidenceDocumentId) : undefined;
  const docUrl = node.kind === 'document' ? safeHttpUrl(typeof node.data.source_url === 'string' ? node.data.source_url : null) : null;
  return (
    <div className="map-info">
      <span className="badge b-muted">{relationBadge(KIND_LABEL[node.kind], RELATION_LABEL[edge.relation] ?? edge.relation)}</span>
      <b>{nodeTitle(node)}</b>
      <dl>
        {evidence && <div><dt>근거 문서</dt><dd>{evidence.label}</dd></div>}
        {edge.evidenceLocation && <div><dt>근거 위치</dt><dd>{edge.evidenceLocation}</dd></div>}
        {edge.reviewStatus && <div><dt>검수 상태</dt><dd>{edge.reviewStatus}</dd></div>}
        {typeof node.data.description === 'string' && node.kind !== 'group' && <div><dt>설명</dt><dd>{node.data.description}</dd></div>}
      </dl>
      {docUrl && <a className="btn btn-o" href={docUrl} target="_blank" rel="noopener noreferrer">문서 원문 ↗</a>}
      {node.kind === 'policy' && <a className="btn btn-o" href={mapHref(node.id)}>이 정책 지도 보기</a>}
    </div>
  );
}

function TreeView({ tree }: { tree: ConditionTree }) {
  return (
    <div className={`ctree l-${tree.logic.toLowerCase()}`}>
      <span className="badge b-muted">{LOGIC_LABEL[tree.logic] ?? tree.logic}</span>
      {(tree.conditions.length > 0 || tree.groups.length > 0) && (
        <ul>
          {tree.conditions.map((c) => <li key={c.id}>{c.label}</li>)}
          {tree.groups.map((g) => <li key={g.id}><TreeView tree={g} /></li>)}
        </ul>
      )}
      {tree.truncated && <p className="map-dim">조건 묶음이 너무 깊거나 반복돼서 여기까지만 보여 드려요.</p>}
    </div>
  );
}
