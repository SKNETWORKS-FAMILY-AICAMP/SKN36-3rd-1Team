import {
  useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent, type PointerEvent, type ReactNode,
} from 'react';
import {
  CENTER_LABEL_MAX, CHAT_MATCH_LIMIT, DRAG_CLICK_TOLERANCE, POLICY_LIST_META_FIELDS, POLICY_META_FIELDS, ungroupedLabel, KIND_LABEL, KIND_ORDER, LIFE_STAGES, LOGIC_LABEL, MAP_ENTER_STAGGER_S, NODE_LABEL_MAX, POLICY_SEARCH_LIMIT,
  RELATION_LABEL, REVIEW_STATUS, SHARED_TILE_LIMIT, SHARED_GROUP_BY, SHARED_GROUP_DEFAULT, SHARED_POLICY_LIMIT, SIMILAR_MIN_SHARED, SIMILAR_POLICY_LIMIT, SIMILAR_RELATIONS, ZOOM_MAX, ZOOM_MIN, ZOOM_STEP,
  type NodeKind,
} from '../config/graph';
import { TOPIC_QUESTION, conditionQuestion, policyQuestion } from '../config/request';
import {
  conditionTrees, policyNeighbors, searchPolicies,
  type ConditionTree, type GraphNode,
} from '../graph/graphModel';
import {
  CENTER_R, INITIAL_VIEW, LABEL_GAP, MAP_HEIGHT, MAP_WIDTH, NODE_R, distinctLabels, layoutNeighbors, shortLabel, zoomAt, zoomCenter, type MapView, type PlacedNeighbor,
} from '../graph/graphLayout';
import { loadGraphDataset, type GraphDataset } from '../graph/loadGraph';
import {
  groupByField, policiesForDocuments, policiesForQuestion, policiesSharing, similarPolicies, type SimilarPolicy,
} from '../graph/relatedPolicies';
import { lifeHref, mapHref } from '../lib/route';
import type { RecentPolicies } from '../lib/recentPolicies';
import { safeHttpUrl } from '../lib/validate';
import { Icon } from './Icon';

type LoadState = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; data: GraphDataset };

/**
 * 정책 관계 지도: 정책 하나를 가운데 두고 Neo4j에 저장된 기관·지역·대상·근거 문서·자격 조건 연결을 보여 준다.
 * 실제 팀 데이터(data/releases)를 그대로 읽는다. 그래프 연결은 정책 구조이지 자격 확정이 아니다.
 */
export default function PolicyMap({ policyId, recent, reduceMotion, onAsk }: {
  policyId: string | null;
  /** 팝업의 '물어보기': 상담 화면으로 가서 질문을 보낸다 (App의 askFromOtherPage) */
  onAsk: (question: string) => void;
  /** 움직임 줄이기 설정 (App에서 한 번 계산해 넘긴다) */
  reduceMotion: boolean;
  /** 상담 화면에서 가장 최근 답변으로 나온 정책. 주소에 정책이 없으면 이 중 첫 정책을 연다 */
  recent: RecentPolicies | null;
}) {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [query, setQuery] = useState('');

  useEffect(() => {
    let alive = true;
    loadGraphDataset()
      .then((data) => { if (alive) setState({ kind: 'ready', data }); })
      .catch(() => { if (alive) setState({ kind: 'error', message: '정책 관계 지도를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.' }); });
    return () => { alive = false; };
  }, []);

  // 방금 상담과 이어지는 정책: 답변의 정책·출처 문서 → (없으면) 질문과 비슷한 정책 검색
  const fromChat = useMemo(() => (state.kind === 'ready' && recent ? chatPolicies(state.data, recent) : null), [state, recent]);
  const openId = policyId ?? fromChat?.policies[0]?.id ?? null;


  if (state.kind === 'loading') {
    return <MapShell><div className="map-msg" role="status">정책 관계 데이터를 불러오고 있어요…</div></MapShell>;
  }
  if (state.kind === 'error') {
    return <MapShell><div className="notice bad"><Icon name="warning" /><div><b>관계 지도를 열지 못했어요</b><p>{state.message}</p></div></div></MapShell>;
  }

  const { index } = state.data;
  const policy = openId ? index.nodes.get(openId) : undefined;
  const searchProps = { data: state.data, query, onQuery: setQuery, currentId: policy?.id ?? null };
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
      {/* 지도를 맨 위에 크게, 정책 카드는 그 아래에 둔다. 다른 정책 찾기는 지도 제목 옆 검색창 */}
      {policy && policy.kind === 'policy'
        ? <PolicyGraph key={policy.id} data={state.data} policy={policy} search={<PolicySearch {...searchProps} />} reduceMotion={reduceMotion} onAsk={onAsk} />
        : (
          <>
            <div className="map-empty">
              <Icon name="search" />
              <b>{policyId ? '이 정책을 데이터에서 찾지 못했어요' : '정책을 골라 주세요'}</b>
              <span>정책을 고르면 기관·지역·대상·근거 문서·자격 조건이 어떻게 연결돼 있는지 보여 드려요.</span>
              <PolicySearch {...searchProps} open />
            </div>
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
          <p>정책의 지원 대상, 담당 기관, 관련 문서를 살펴보세요</p>
        </div>
        {/* 데이터가 덜 받아졌거나 섞였을 때만 위에서 알린다. 평소 버전·건수는 맨 아래 작은 글씨 */}
        {mismatch && <p className="map-meta"><span className="badge b-warn">일부 정책 정보가 빠져 있을 수 있어요</span></p>}
        {children}
        <p className="map-note">관계가 연결돼 있어도 자격이 확정된 건 아니에요. 최종 자격과 신청 가능 여부는 공식 원문에서 확인해 주세요.</p>
        {data && counted && (
          <p className="map-data">
            정책 {data.index.policies.length.toLocaleString('ko-KR')}개
          </p>
        )}
      </div>
    </section>
  );
}

/** 정책 이름·분야·지역 한 줄 (지도 제목·검색 결과·정책 카드가 같이 쓴다) */
const policyMeta = (p: GraphNode, keys: string[]) => keys.map((k) => p.data[k]).filter((v): v is string => typeof v === 'string').join(' · ');

/**
 * 다른 정책 찾기. 지도 제목 옆의 검색창이고, 입력하면 결과가 아래로 펼쳐진다 (고르거나 Esc면 닫힘).
 * open: 정책을 아직 고르지 않은 빈 화면에서는 결과를 늘 보여 준다. 결과는 보일 때만 계산한다.
 */
function PolicySearch({ data, query, onQuery, currentId, open }: {
  data: GraphDataset; query: string; onQuery: (v: string) => void; currentId: string | null; open?: boolean;
}) {
  const show = open || query.trim() !== '';
  const results = useMemo(() => (show ? searchPolicies(data.index, query, POLICY_SEARCH_LIMIT) : []), [data, query, show]);
  return (
    <div className={'map-find' + (open ? ' static' : '')} role="search">
      <input className="map-search" value={query} onChange={(e) => onQuery(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Escape') onQuery(''); }}
        placeholder="다른 정책 찾기 (이름·지역·분야)" aria-label="정책 찾기" />
      {show && (
        <div className="map-find-pop">
          <ul className="map-list">
            {results.map((p) => (
              <li key={p.id}>
                <a href={mapHref(p.id)} aria-current={p.id === currentId ? 'page' : undefined} onClick={() => onQuery('')}>
                  <b>{p.label}</b>
                  <small>{policyMeta(p, POLICY_LIST_META_FIELDS)}</small>
                </a>
              </li>
            ))}
            {results.length === 0 && <li className="map-none">찾는 정책이 없어요</li>}
          </ul>
          {results.length === POLICY_SEARCH_LIMIT && <p className="map-more">{POLICY_SEARCH_LIMIT}개까지만 보여 드려요. 더 좁혀 찾아보세요.</p>}
        </div>
      )}
    </div>
  );
}

/** 정책 하나의 지도. 정책이 바뀌면 key로 새로 그려서 열린 팝업·선택이 자연스럽게 초기화된다 */
function PolicyGraph({ data, policy, search, reduceMotion, onAsk }: {
  data: GraphDataset; policy: GraphNode; search: ReactNode; reduceMotion: boolean; onAsk: (question: string) => void;
}) {
  // 누른 둘레 원의 번호: 그 원을 강조하고 팝업으로 자세히 보여 준다 (null이면 팝업 닫힘).
  // 같은 노드가 다른 관계로 두 번 놓일 수 있어 노드 ID 대신 놓인 순서로 고른다.
  const [sel, setSel] = useState<number | null>(null);
  const cardRef = useRef<HTMLElement>(null);
  const closeDialog = () => setSel(null);
  const { index } = data;
  const neighbors = useMemo(() => policyNeighbors(index, policy.id), [index, policy.id]);
  const trees = useMemo(() => conditionTrees(index, policy.id), [index, policy.id]);
  const { center, placed } = useMemo(() => layoutNeighbors(neighbors), [neighbors]);
  // 정책마다 한 번만 계산
  const similar = useMemo(
    () => similarPolicies(index, policy.id, SIMILAR_RELATIONS, SIMILAR_MIN_SHARED, SIMILAR_POLICY_LIMIT),
    [index, policy.id],
  );
  const selected = sel != null ? placed[sel] ?? null : null;
  // 이전·다음: 둘레 원을 시계 방향으로 돈다 (마지막 다음은 처음)
  const total = placed.length;
  const step = (d: number) => setSel((s) => (s == null ? s : (s + d + total) % total));
  // 가운데 원: 같은 내용을 팝업으로 또 띄우지 않고, 아래 정책 카드로 내려가 잠깐 반짝여 위치를 알려 준다
  const showCard = () => {
    const card = cardRef.current;
    if (!card) return;
    card.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' });
    card.focus({ preventScroll: true });
    // 같은 클래스를 다시 붙여도 애니메이션이 처음부터 돌도록 한 번 떼고 다시 붙인다
    card.classList.remove('flash');
    void card.offsetWidth;
    card.classList.add('flash');
  };
  // 범례: 가운데 정책 + 둘레에 실제로 있는 종류 (정책끼리 연결돼도 '정책'은 한 번만)
  const kinds = [...new Set(['policy' as const, ...placed.map((n) => n.node.kind)])];
  // 지도 제목과 정책 카드가 같이 쓴다
  const meta = policyMeta(policy, POLICY_META_FIELDS);
  // 둘레 원 이름: 잘라서 같아지는 이름(같은 기관의 문서 여러 개 등)은 공통 앞부분을 떼고 다른 부분을 보여 준다
  const labels = useMemo(
    () => distinctLabels(placed.map((n) => (n.node.kind === 'group' ? KIND_LABEL.group : n.node.label)), NODE_LABEL_MAX),
    [placed],
  );

  return (
    <div className="map-main">
      <div className="map-head">
        <div className="map-title">
          <b>{policy.label}</b>
          <small>{meta} · 연결 {neighbors.length}개</small>
        </div>
        {search}
      </div>

      <div className="map-stage">
        <ZoomCanvas resetKey={policy.id} label={`${policy.label} 관계 지도`}>
          {/*
            정책을 열 때(또는 바꿀 때) 한 번 펼쳐지는 등장 효과: key로 정책마다 새로 그려 애니메이션을 다시 시작한다.
            이웃 하나(선·관계 이름·원)를 한 묶음으로 둬서, 마우스를 올린 묶음만 진하게 하고 나머지는 CSS로 흐리게 한다.
          */}
          <g key={policy.id} className={'map-graph' + (reduceMotion ? '' : ' enter')}>
            {placed.map((n, i) => (
              <g key={`${n.node.id}-${n.edge.relation}`} className="nb" style={{ '--d': `${i * MAP_ENTER_STAGGER_S}s` } as CSSProperties}>
                <line x1={center.x} y1={center.y} x2={n.x} y2={n.y} pathLength={1} className={'gl' + (i === sel ? ' on' : '')} />
                <EdgeLabel n={n} cx={center.x} cy={center.y} />
                <MapNode x={n.x} y={n.y} r={NODE_R} kind={n.node.kind}
                  label={labels[i]}
                  title={n.node.kind === 'group' ? nodeTitle(n.node) : `${RELATION_LABEL[n.edge.relation] ?? '관련 정보'}: ${n.node.label}`}
                  active={i === sel} onSelect={() => setSel(i)} />
              </g>
            ))}
            {/* 가운데 정책: 뒤에서 옅은 빛 테두리가 천천히 퍼진다 (중심 표시) */}
            <circle className="pulse" cx={center.x} cy={center.y} r={CENTER_R} aria-hidden="true" />
            <MapNode x={center.x} y={center.y} r={CENTER_R} kind="policy" label={shortLabel(policy.label, CENTER_LABEL_MAX)} title={`${policy.label} (아래 정책 설명으로 이동)`}
              active={false} onSelect={showCard} center />
          </g>
        </ZoomCanvas>
        <ul className="map-legend" aria-label="범례">
          {kinds.map((k) => <li key={k}><i className={`k-${k}`} />{KIND_LABEL[k]}</li>)}
        </ul>
      </div>

      {/* 지도 아래 정책 카드 한 장: 요약(긴 글은 3줄) → 자격 조건 | 비슷한 정책. 기관·분야 등 연결은 지도가 보여 준다 */}
      <article ref={cardRef} className="pcard-map" tabIndex={-1} aria-label={`${policy.label} 정책 설명`}
        onAnimationEnd={(e) => { if (e.target === e.currentTarget) e.currentTarget.classList.remove('flash'); }}>
        <header className="pc-head">
          <div>
            <span className="pd-kind">정책</span>
            <h3>{policy.label}</h3>
            <p className="pc-meta">{meta}</p>
          </div>
          <div className="pc-actions">
            <button type="button" className="btn btn-p" onClick={() => onAsk(policyQuestion(policy.label))} title={policyQuestion(policy.label)}>상담에서 물어보기</button>
            <SourceLink policy={policy} />
          </div>
        </header>
        <PolicyFacts policy={policy} />
        <div className="pc-split">
          <section>
            <h4>자격 조건</h4>
            <Conditions trees={trees} />
          </section>
          <section>
            <h4>연결 구조가 비슷한 정책</h4>
            <SimilarPolicies similar={similar} />
          </section>
        </div>
        <p className="map-dim">데이터에 정리된 내용이에요. 지도의 둘레 원을 누르면 그 연결을 자세히 보여 드려요.</p>
      </article>

      {/* 원을 누르면 지도 위에 뜨는 팝업. 이미 읽은 데이터만 쓰고, 열려 있을 때만 내용을 그린다 */}
      <NodeDialog open={sel != null} title={selected ? nodeTitle(selected.node) : policy.label} onClose={closeDialog}
        onStep={total > 1 ? step : undefined} position={sel == null ? '' : `${sel + 1} / ${total}`}
        action={selected && <AskButton question={askQuestion(selected.node, policy)} onAsk={onAsk} />}>
        {selected && (
          <NodeDetail key={sel} n={selected} data={data} policy={policy}>
            {selected.node.kind === 'group' && (
              <section className="pd-block">
                <h4>자격 조건 구조</h4>
                <Conditions trees={trees.filter((t) => t.id === selected.node.id)} />
              </section>
            )}
          </NodeDetail>
        )}
      </NodeDialog>
    </div>
  );
}

/**
 * 지도 위에 뜨는 팝업 (브라우저 기본 <dialog>: 초점 가두기·Esc 닫기·닫은 뒤 초점 되돌리기를 기본 제공).
 * 바깥(어두운 배경)을 누르거나 ✕·Esc로 닫는다. 닫혀 있으면 내용을 그리지 않는다.
 */
function NodeDialog({ open, title, onClose, onStep, position, action, children }: {
  open: boolean; title: string; onClose: () => void;
  /** 이전(-1)·다음(+1) 원으로 넘기기. 없으면 넘기기 버튼을 숨긴다 */
  onStep?: (d: number) => void; position: string;
  /** 아래 줄 오른쪽에 둘 버튼 (상담에서 물어보기) */
  action?: ReactNode; children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  // Esc 등 브라우저가 직접 닫을 때도 화면 상태를 맞춘다. React 18의 onClose는 dialog에서 확실히 오지 않아 직접 등록한다.
  // 최신 onClose는 ref에 두어 렌더마다 이벤트를 다시 등록하지 않는다
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const handle = () => onCloseRef.current();
    dialog.addEventListener('close', handle);
    return () => dialog.removeEventListener('close', handle);
  }, []);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog ref={ref} className="map-dialog" aria-label={title}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      onKeyDown={(e) => {
        if (!onStep || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
        e.preventDefault();
        onStep(e.key === 'ArrowLeft' ? -1 : 1);
      }}>
      {open && (
        <div className="map-dialog-body">
          <button type="button" className="map-dialog-x" onClick={onClose} aria-label="닫기">✕</button>
          {/* 넘길 때마다 내용 칸이 다시 올라오도록 위치를 key로 쓴다 */}
          <div key={position} className="map-dialog-content">{children}</div>
          {(onStep || action) && (
            <div className="map-dialog-nav">
              {onStep && (
                <div className="nav-step">
                  <button type="button" className="btn btn-o" onClick={() => onStep(-1)} aria-label="이전 원">←</button>
                  <span aria-live="polite">{position}</span>
                  <button type="button" className="btn btn-o" onClick={() => onStep(1)} aria-label="다음 원">→</button>
                </div>
              )}
              {action}
            </div>
          )}
        </div>
      )}
    </dialog>
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
  return node.kind === 'group' ? `${KIND_LABEL.group} · ${LOGIC_LABEL[node.label] ?? '공식 공고 확인 필요'}` : node.label;
}

function MapNode({ x, y, r, kind, label, title, active, onSelect, center }: {
  x: number; y: number; r: number; kind: string; label: string; title: string; active: boolean; onSelect: () => void; center?: boolean;
}) {
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(); } };
  return (
    <g className={`gn k-${kind}${active ? ' on' : ''}${center ? ' center' : ''}`} transform={`translate(${x} ${y})`}
      role="button" tabIndex={0} aria-label={title} aria-pressed={active} onClick={onSelect} onKeyDown={onKey}>
      <title>{title}</title>
      <g className="pop">
        <circle r={r} />
        <text y={center ? 4 : r + LABEL_GAP} textAnchor="middle">{label}</text>
      </g>
    </g>
  );
}

function EdgeLabel({ n, cx, cy }: { n: PlacedNeighbor; cx: number; cy: number }) {
  const label = RELATION_LABEL[n.edge.relation] ?? '관련 정보';
  return <text className="ge" x={(cx + n.x) / 2} y={(cy + n.y) / 2} textAnchor="middle">{label}</text>;
}

/** 정책 요약 항목: 화면 이름 → 정책 노드의 필드 (data_contract의 정책 필드) */
const POLICY_FACTS: [string, string][] = [['지원 대상', 'eligibility'], ['지원 내용', 'benefit'], ['신청 기간', 'application_period']];

function PolicyFacts({ policy }: { policy: GraphNode }) {
  const rows = POLICY_FACTS
    .map(([label, key]) => [label, policy.data[key]] as const)
    .filter((row): row is readonly [string, string] => typeof row[1] === 'string' && row[1].trim() !== '');
  if (rows.length === 0) return null;
  return (
    <dl className="pc-facts">
      {rows.map(([label, text]) => <div key={label}><dt>{label}</dt><dd><Clamp text={text} /></dd></div>)}
    </dl>
  );
}

/** 긴 글은 몇 줄(CSS --clamp-lines)만 보여 주고, 실제로 넘칠 때만 '더 보기'를 단다 */
function Clamp({ text }: { text: string }) {
  const ref = useRef<HTMLParagraphElement>(null);
  const [open, setOpen] = useState(false);
  const [long, setLong] = useState(false);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el && !open) setLong(el.scrollHeight > el.clientHeight + 1);
  }, [text, open]);
  return (
    <>
      <p ref={ref} className={'clamp' + (open ? ' open' : '')}>{text}</p>
      {long && <button type="button" className="pd-more" onClick={() => setOpen((v) => !v)} aria-expanded={open}>{open ? '접기' : '더 보기'}</button>}
    </>
  );
}

function sourceUrl(node?: GraphNode) {
  const url = node?.data.source_url;
  return safeHttpUrl(typeof url === 'string' ? url : null);
}

function SourceLink({ policy, compact = false }: { policy: GraphNode; compact?: boolean }) {
  const source = sourceUrl(policy);
  return source ? <a className={compact ? 'pd-source' : 'btn btn-o'} href={source} target="_blank" rel="noopener noreferrer"
    aria-label={compact ? `${policy.label} 공식 원문 (새 탭)` : undefined}>공식 원문 ↗</a> : null;
}

/** 자격 조건: 원천 데이터에 조건이 정리되지 않은 묶음(UNKNOWN, 비어 있음)만 있으면 트리 대신 한 줄 안내 */
function Conditions({ trees }: { trees: ConditionTree[] }) {
  if (trees.length === 0) return <p className="pc-note">연결된 자격 조건 묶음이 없어요.</p>;
  const empty = trees.every((t) => t.logic === 'UNKNOWN' && t.conditions.length === 0 && t.groups.length === 0);
  if (empty) return <p className="pc-note">원천 데이터에 조건이 정리되지 않아 자동으로 판정하지 않아요. 공식 원문에서 확인해 주세요.</p>;
  return <>{trees.map((t) => <TreeView key={t.id} tree={t} />)}</>;
}

/** 팝업·상세 머리: 지도와 같은 색 원 + 종류 + 이름 */
function PopHead({ kind, title }: { kind: NodeKind; title: string }) {
  return (
    <div className="pd-head">
      <i className={`pd-dot k-${kind}`} aria-hidden="true" />
      <div>
        <span className="pd-kind">{KIND_LABEL[kind]}</span>
        <b>{title}</b>
      </div>
    </div>
  );
}

/**
 * 둘레 원 팝업: 무엇인지 한 줄 → 분야별 관련 정책 수 (타일을 누르면 해당 분야만 표시)
 * → 정책 목록(이 정책과 같은 분야 먼저) → 맨 아래 근거 한 줄.
 * 같은 말을 되풀이하지 않도록 제목이 그대로 들어 있는 설명은 숨긴다. '받을 수 있는' 같은 자격 표현은 쓰지 않는다.
 */
function NodeDetail({ n, data, policy, children }: { n: PlacedNeighbor; data: GraphDataset; policy: GraphNode; children?: ReactNode }) {
  const { node, edge } = n;
  const [showAll, setShowAll] = useState(false);
  const [filter, setFilter] = useState<string | null>(null);
  const [preview, setPreview] = useState<GraphNode | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const backRef = useRef<HTMLButtonElement>(null);
  const returnPoint = useRef<{ scrollTop: number; policyId: string } | null>(null);
  useLayoutEffect(() => {
    const dialog = contentRef.current?.closest('dialog');
    if (!dialog || !returnPoint.current) return;
    if (preview) {
      dialog.scrollTop = 0;
      backRef.current?.focus({ preventScroll: true });
    } else {
      const { scrollTop, policyId } = returnPoint.current;
      contentRef.current?.querySelector<HTMLButtonElement>(`[data-preview-id="${CSS.escape(policyId)}"]`)?.focus({ preventScroll: true });
      dialog.scrollTop = scrollTop;
      returnPoint.current = null;
    }
  }, [preview]);
  const { index } = data;
  const relation = RELATION_LABEL[edge.relation] ?? '관련 정보';
  // 머리에 종류('자격 조건')가 이미 있으니 조건 묶음은 논리만 제목으로 쓴다
  const title = node.kind === 'group' ? LOGIC_LABEL[node.label] ?? '공식 공고 확인 필요' : node.label;
  // 근거 문서 원이면 그 문서가 곧 근거라 문서 이름을 또 쓰지 않는다
  const evidence = edge.evidenceDocumentId && edge.evidenceDocumentId !== node.id ? index.nodes.get(edge.evidenceDocumentId) : undefined;
  const evidenceUrl = sourceUrl(evidence);
  const docUrl = node.kind === 'document' ? sourceUrl(node) : null;
  const review = edge.reviewStatus ? REVIEW_STATUS[edge.reviewStatus] : undefined;
  const stage = node.kind === 'lifestage' ? LIFE_STAGES.find((s) => s.id === node.id) : undefined;
  const ageRange = typeof node.data.age_range === 'string' ? node.data.age_range : null;
  const rawDescription = typeof node.data.description === 'string' && node.kind !== 'group' ? node.data.description : null;
  const description = rawDescription && !rawDescription.includes(node.label) ? rawDescription : null;
  // 무엇인지 한 줄: 정책 → 이 원이면 'OO 정책의 지원 대상', 다른 정책이 이 정책을 가리키면 관계 이름만
  const context = <p className="pd-meta">현재 정책 · {policy.label} › {relation} · {title}</p>;
  const group = SHARED_GROUP_BY[node.kind] ?? SHARED_GROUP_DEFAULT;
  const mine = typeof policy.data[group.key] === 'string' ? policy.data[group.key] as string : null;
  // 팝업이 열렸을 때 한 번만 계산한다 (정책·조건 묶음은 다른 정책과 공유하는 노드가 아니라 건너뛴다)
  const { shared, groups, ordered } = useMemo(() => {
    const list = node.kind === 'policy' || node.kind === 'group' ? [] : policiesSharing(index, node.id, policy.id);
    const grouped = groupByField(list.map((p) => ({ policy: p })), group.key, ungroupedLabel(group.label));
    // 목록은 이 정책과 같은 분야(지역)부터. 같은 묶음 안에서는 이름순(policiesSharing 순서)을 그대로 둔다
    const same = grouped.find((g) => g.value === mine)?.items.map((i) => i.policy) ?? [];
    const sameIds = new Set(same.map((p) => p.id));
    return { shared: list, groups: grouped, ordered: [...same, ...list.filter((p) => !sameIds.has(p.id))] };
  }, [index, node.id, node.kind, policy.id, group.key, group.label, mine]);
  const listed = filter ? groups.find((g) => g.value === filter)?.items.map((i) => i.policy) ?? [] : ordered;
  const shown = showAll ? listed : listed.slice(0, SHARED_POLICY_LIMIT);
  const hiddenGroups = groups.length - SHARED_TILE_LIMIT;

  if (preview) return (
    <div className="map-info" ref={contentRef}>
      {context}
      <button ref={backRef} type="button" className="pd-more" onClick={() => setPreview(null)}>← 관련 정책 목록</button>
      <PopHead kind="policy" title={preview.label} />
      <PolicyFacts policy={preview} />
      <div className="pd-actions">
        <SourceLink policy={preview} />
        <a className="btn btn-o" href={mapHref(preview.id)}>관계 지도에서 보기</a>
      </div>
    </div>
  );

  return (
    <div className="map-info" ref={contentRef}>
      {context}
      <PopHead kind={node.kind} title={title} />
      {node.kind === 'policy' && <PolicyFacts policy={node} />}

      {(description || ageRange) && (
        <section className="pd-block">
          {description && <p className="pd-text">{description}</p>}
          {ageRange && <p className="pd-text">데이터의 나이 구분 {ageRange} <small>· 분류 기준이지 자격 조건이 아니에요</small></p>}
        </section>
      )}

      {children}

      {shared.length > 0 && (
        <>
          <section className="pd-block">
            <h4>관련 정책 <span className="pd-count">{shared.length.toLocaleString('ko-KR')}개</span> · {group.label}별</h4>
            <ul className="pd-tiles">
              {groups.slice(0, SHARED_TILE_LIMIT).map((g) => (
                <li key={g.value}>
                  <button type="button" aria-pressed={filter === g.value}
                    onClick={() => { setFilter((f) => (f === g.value ? null : g.value)); setShowAll(false); }}>
                    <span className="pd-tile-label">{g.value}</span>
                    <span className="pd-tile-summary">
                      <strong>{g.items.length.toLocaleString('ko-KR')}<small>개</small></strong>
                      {g.value === mine && <span className="pd-list-badge">현재 정책</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {hiddenGroups > 0 && <p className="pd-meta">그 외 {hiddenGroups}개 {group.label}</p>}
          </section>

          <section className="pd-block">
            <h4>
              {filter ?? '전체 정책'} <span className="pd-count">{listed.length.toLocaleString('ko-KR')}개</span>
              {filter && <button type="button" className="pd-clear" onClick={() => { setFilter(null); setShowAll(false); }}>필터 해제</button>}
            </h4>
            <ul className="pd-plist">
              {shown.map((p) => {
                const value = p.data[group.key];
                return (
                  <li key={p.id}>
                    <button type="button" data-preview-id={p.id} onClick={(event) => {
                      returnPoint.current = { scrollTop: event.currentTarget.closest('dialog')?.scrollTop ?? 0, policyId: p.id };
                      setPreview(p);
                    }}>
                      <span>{p.label}</span>
                      <small className="pd-list-badge">{typeof value === 'string' && value.trim() ? value : ungroupedLabel(group.label)}</small>
                    </button>
                    <SourceLink policy={p} compact />
                  </li>
                );
              })}
            </ul>
            {listed.length > SHARED_POLICY_LIMIT && (
              <button type="button" className="pd-more" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
                {showAll ? '접기 ▴' : `모두 보기 (${listed.length.toLocaleString('ko-KR')}개) ▾`}
              </button>
            )}
          </section>
        </>
      )}

      {(docUrl || stage || node.kind === 'policy') && (
        <div className="pd-actions">
          {docUrl && <a className="btn btn-o" href={docUrl} target="_blank" rel="noopener noreferrer">문서 원문 ↗</a>}
          {stage && <a className="btn btn-o" href={lifeHref(stage.name)}>생애주기에서 {stage.name} 보기</a>}
          {node.kind === 'policy' && <SourceLink policy={node} />}
          {node.kind === 'policy' && <a className="btn btn-o" href={mapHref(node.id)}>관계 지도에서 보기</a>}
        </div>
      )}

      {(evidence || edge.evidenceLocation || edge.reviewStatus) && (
        <p className="pd-meta pd-foot">
          <span>근거</span>
          {evidence && (evidenceUrl
            ? <a href={evidenceUrl} target="_blank" rel="noopener noreferrer">{evidence.label} ↗</a>
            : <span>{evidence.label}</span>)}
          {edge.evidenceLocation && <span>원문 ‘{edge.evidenceLocation}’ 항목</span>}
          {edge.reviewStatus && (
            <span className={`pd-status ${review?.tone ?? 'muted'}`} title={review?.label ?? '원문 확인 필요'}>
              {review?.short ?? '원문 확인 필요'}
            </span>
          )}
        </p>
      )}
    </div>
  );
}

/** 둘레 원에서 상담으로 보낼 질문: 정책은 정책 질문, 조건 묶음은 자격 조건 질문, 나머지는 종류별 주제 질문(없으면 가운데 정책 질문) */
function askQuestion(node: GraphNode, policy: GraphNode): string {
  if (node.kind === 'policy') return policyQuestion(node.label);
  if (node.kind === 'group') return conditionQuestion(policy.label);
  return TOPIC_QUESTION[node.kind]?.(node.label) ?? policyQuestion(policy.label);
}

/** 상담 화면으로 가서 질문 보내기. 보낼 질문은 마우스를 올리면(그리고 화면 읽기 프로그램에) 보여 준다 */
function AskButton({ question, onAsk }: { question: string; onAsk: (question: string) => void }) {
  return (
    <button type="button" className="btn btn-o pd-ask" onClick={() => onAsk(question)}
      title={`“${question}” 보내기`} aria-label={`상담에서 물어보기: ${question}`}>
      상담에서 물어보기
    </button>
  );
}

/** 겹친 노드를 종류별로: '대상 청년'과 '생애단계 청년'처럼 이름이 같아도 구분되게 한다 */
function sharedByKind(nodes: GraphNode[]): string {
  return KIND_ORDER
    .map((k) => [k, nodes.filter((n) => n.kind === k).map((n) => n.label)] as const)
    .filter(([, names]) => names.length > 0)
    .map(([k, names]) => `${KIND_LABEL[k]} ${names.join(', ')}`)
    .join(' · ');
}

/** 기관·지역·분야·대상·생애단계가 많이 겹치는 정책 (계산은 PolicyGraph에서 한 번) */
function SimilarPolicies({ similar }: { similar: SimilarPolicy[] }) {
  if (similar.length === 0) return <p className="pc-note">연결이 많이 겹치는 정책이 없어요.</p>;
  return (
    <>
      <ol className="pc-similar">
        {similar.map(({ policy: p, shared }) => {
          // 무엇이 겹쳤는지는 마우스를 올리면 보여 준다 (화면은 이름과 개수만). 문장은 한 번만 만든다
          const overlap = sharedByKind(shared);
          return (
            <li key={p.id} title={overlap}>
              <a href={mapHref(p.id)}>{p.label}</a>
              <span className="pc-n" aria-label={`연결 ${shared.length}개 겹침: ${overlap}`}>{shared.length}</span>
            </li>
          );
        })}
      </ol>
      <p className="pc-note">기관·지역·분야·대상·생애단계 연결이 겹친 개수예요. 자격이 비슷하다는 뜻은 아니에요.</p>
    </>
  );
}

function TreeView({ tree }: { tree: ConditionTree }) {
  return (
    <div className={`ctree l-${tree.logic.toLowerCase()}`}>
      <span className="badge b-muted">{LOGIC_LABEL[tree.logic] ?? '공식 공고 확인 필요'}</span>
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
