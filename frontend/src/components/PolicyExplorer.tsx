import { useEffect, useId, useMemo, useRef, useState } from 'react';
import type { AskResponse } from '../api/types';
import { catalogPolicies, type CatalogPolicy } from '../lib/policyCatalog';
import { scheduledPolicies, type ScheduleFilter } from '../lib/applicationSchedule';
import { safeHttpUrl } from '../lib/validate';
import { PolicyCard } from './TurnView';
import { useSavedPolicies } from './SavedPolicies';
import { useScheduleDay } from './ScheduleClock';
import { loadPolicyCatalog, POLICY_API_URL } from '../api/policyCatalog';
import { COMPARE_MAX, COMPARE_MIN } from '../lib/policyCompare';
import { PolicyCompare } from './PolicyCompare';

// 상담 화면이 너무 길어지지 않도록 처음엔 두 줄(데스크톱 3열 기준)만 보여 준다
const PAGE_SIZE = 6;
const filters: [ScheduleFilter, string][] = [['all', '전체'], ['open', '접수 중'], ['soon', '7일 이내 마감'], ['before', '접수 예정'], ['ongoing', '상시 접수']];
export function PolicyExplorer({ response, onAsk, onDemoApply, onOpenMap, active = true }: {
  response: AskResponse | null; onAsk: (question: string, origin: HTMLElement, policyId?: string) => void;
  onDemoApply: () => void; onOpenMap?: () => void;
  /** 상담 화면이 보이는 중인지. false면 포커스·탭 복귀 갱신을 건너뛴다 */
  active?: boolean;
}) {
  const activeRef = useRef(active);
  activeRef.current = active;
  const [catalog, setCatalog] = useState<CatalogPolicy[]>([]), [version, setVersion] = useState('');
  const [load, setLoad] = useState<'loading' | 'ready' | 'error'>('loading'), [attempt, setAttempt] = useState(0);
  const [tab, setTab] = useState<'all' | 'saved'>('all');
  const [query, setQuery] = useState(''), [region, setRegion] = useState(''), [category, setCategory] = useState('');
  const [filter, setFilter] = useState<ScheduleFilter>('all'), [sort, setSort] = useState<'deadline' | 'name'>('deadline');
  const [limit, setLimit] = useState(PAGE_SIZE);
  const [evaluatedOn, setEvaluatedOn] = useState('');
  // 정책 비교: 고르기 모드, 고른 정책 ID(고른 순서), 비교 화면 열림 여부
  const [picking, setPicking] = useState(false), [picked, setPicked] = useState<string[]>([]), [comparing, setComparing] = useState(false);
  const sectionRef = useRef<HTMLElement>(null);
  const state = useSavedPolicies(), today = useScheduleDay(), group = useId();
  useEffect(() => {
    let alive = true, pending = false;
    const controller = new AbortController();
    async function refresh() {
      if (pending || document.hidden) return;
      pending = true; setLoad('loading');
      try {
        if (POLICY_API_URL) {
          const data = await loadPolicyCatalog(controller.signal);
          if (alive) {
            setCatalog(data.items.map(item => ({ policy: { ...item, conditions: [] }, regions: item.regions, categories: item.categories })));
            setVersion(data.dataset_version); setEvaluatedOn(data.evaluated_on); setLoad('ready');
          }
        } else {
          const { loadGraphDataset } = await import('../graph/loadGraph');
          const data = await loadGraphDataset();
          if (alive) { setCatalog(catalogPolicies(data.index)); setVersion(data.version); setLoad('ready'); }
        }
      } catch { if (alive) setLoad('error'); }
      finally { pending = false; }
    }
    void refresh();
    // 상담 화면이 숨겨진 동안(관계 지도·생애주기)은 창 포커스·탭 복귀로 목록을 다시 부르지 않는다
    const visible = () => { if (!document.hidden && activeRef.current) void refresh(); };
    window.addEventListener('focus', visible); document.addEventListener('visibilitychange', visible);
    return () => { alive = false; controller.abort(); window.removeEventListener('focus', visible); document.removeEventListener('visibilitychange', visible); };
  }, [attempt, today]);
  useEffect(() => setLimit(PAGE_SIZE), [tab, query, region, category, filter, sort]);
  const policies = useMemo(() => {
    const latest = new Map((!POLICY_API_URL && response && !response.is_sample ? response.policy_checks : []).map(p => [p.policy_id, p]));
    return catalog.map(row => {
      const fresh = latest.get(row.policy.policy_id);
      // 개인 판정은 목록에 복사하지 않는다. 일정·문의처만 서버 응답을 사용한다.
      return { ...row, policy: fresh ? { ...row.policy, reception_status: fresh.reception_status,
        application_schedule: fresh.application_schedule, contacts: fresh.contacts } : row.policy };
    });
  }, [catalog, response]);
  const compareInputs = useMemo(() => picked.flatMap(id => policies.find(row => row.policy.policy_id === id) ?? []), [picked, policies]);
  const togglePick = (id: string) => setPicked(prev => prev.includes(id) ? prev.filter(x => x !== id) : prev.length >= COMPARE_MAX ? prev : [...prev, id]);
  const stopPicking = () => { setPicking(false); setPicked([]); };
  const openCompare = () => { setComparing(true); sectionRef.current?.scrollIntoView({ block: 'start' }); };
  const savedIds = new Set(state?.saved.map(p => p.policy_id));
  const catalogIds = new Set(catalog.map(row => row.policy.policy_id));
  const missingSaved = (state?.saved ?? []).filter(p => !catalogIds.has(p.policy_id));
  const options = useMemo(() => ({ regions: [...new Set(catalog.flatMap(r => r.regions))].sort(), categories: [...new Set(catalog.flatMap(r => r.categories))].sort() }), [catalog]);
  const normalize = (text: string) => text.toLocaleLowerCase().replace(/\s/g, '');
  const q = normalize(query);
  const rows = scheduledPolicies(policies.filter(row => (tab !== 'saved' || savedIds.has(row.policy.policy_id))
    && (!region || row.regions.includes(region)) && (!category || row.categories.includes(category))
    && normalize(`${row.policy.policy_name} ${row.policy.agency}`).includes(q)).map(row => row.policy), today, filter, sort);
  const soon = load === 'ready' && (!POLICY_API_URL || evaluatedOn === today) ? scheduledPolicies(policies.filter(row => savedIds.has(row.policy.policy_id)).map(row => row.policy), today, 'soon', 'deadline') : [];
  if (comparing && compareInputs.length >= COMPARE_MIN) {
    return <section className="schedule-section" id="policy-explorer" ref={sectionRef}><div className="wrap">
      <div className="board-h"><h2>놓치지 않도록 <span>정책 둘러보기</span></h2></div>
      <PolicyCompare inputs={compareInputs} onAsk={onAsk} onOpenMap={onOpenMap}
        onBack={() => { setComparing(false); sectionRef.current?.scrollIntoView({ block: 'start' }); }} />
    </div></section>;
  }
  return <section className="schedule-section" id="policy-explorer" ref={sectionRef}><div className="wrap">
    <div className="board-h"><h2>놓치지 않도록 <span>정책 둘러보기</span></h2><p>관심 정책을 찾고, 내 조건은 상담에서 확인해 보세요.</p></div>
    {soon.length > 0 && <div className="notice info" role="status"><div><b>저장한 정책 {soon.length}개가 7일 이내 마감돼요</b><p>{soon.map(row => row.policy.policy_name).join(' · ')}</p><button className="chip" type="button" onClick={() => { setTab('saved'); setFilter('soon'); setQuery(''); setRegion(''); setCategory(''); }}>마감 임박 정책 보기</button></div></div>}
    {/* 검색을 가장 먼저, 범위·조건 필터는 그 아래 한 묶음으로 둔다 */}
    <div className="explorer-search" role="search">
      <label htmlFor={`${group}-q`}>정책 검색</label>
      <input id={`${group}-q`} value={query} onChange={e => setQuery(e.target.value)} placeholder="정책명·기관명으로 찾기" type="search" />
    </div>
    <div className="explorer-filters">
      <div className="schedule-tools" role="group" aria-label="정책 목록 범위">
        <button className="chip" type="button" aria-pressed={tab === 'all'} onClick={() => setTab('all')}>전체 정책 {catalog.length}</button>
        <button className="chip" type="button" aria-pressed={tab === 'saved'} onClick={() => setTab('saved')}>★ 관심 정책 {state?.saved.length ?? 0}</button>
        {!picking
          ? <button className="btn btn-o compare-start" type="button" onClick={() => setPicking(true)}>비교할 정책 고르기</button>
          : <button className="btn btn-o compare-start" type="button" onClick={stopPicking}>비교 고르기 취소</button>}
      </div>
      <div className="schedule-tools explorer-selects">
        <label>지역 <select value={region} onChange={e => setRegion(e.target.value)}><option value="">전체 지역</option>{options.regions.map(x => <option key={x}>{x}</option>)}</select></label>
        <label>분야 <select value={category} onChange={e => setCategory(e.target.value)}><option value="">전체 분야</option>{options.categories.map(x => <option key={x}>{x}</option>)}</select></label>
        <label>정렬 <select value={sort} onChange={e => setSort(e.target.value as typeof sort)}><option value="deadline">마감 임박순</option><option value="name">이름순</option></select></label>
      </div>
      <div className="schedule-tools" role="group" aria-label="전체 정책 접수 상태">{filters.map(([key, text]) => <button key={key} className="chip" type="button" aria-pressed={filter === key} onClick={() => setFilter(key)}>{text}</button>)}</div>
    </div>
    <p className="schedule-scope">{version ? `배포 데이터 ${version} 기준` : '배포 정책 목록'} · {POLICY_API_URL ? `서버의 정책 파일 기준 · 일정 확인일 ${evaluatedOn || '확인 중'}. 검색 DB의 반영 상태와는 다를 수 있어요.` : '전체 일정 API 미연결 · 일정은 오늘 확인된 상담 응답이 있는 정책만 표시합니다.'} 목록은 신청 자격 판정이 아닙니다.</p>
    {tab === 'saved' && <p className="schedule-scope">저장은 이 브라우저·기기에서만 유지돼요. 재방문·탭 복귀 시 일정을 다시 확인하고, 확인에 실패하면 마감 안내를 숨겨요.</p>}
    {state?.error && <p role="alert">{state.error}</p>}
    {load === 'loading' && <p role="status">정책 목록을 불러오고 있어요…</p>}
    {load === 'error' && <div role="alert">정책 목록을 불러오지 못했어요. <button className="chip" type="button" onClick={() => setAttempt(x => x + 1)}>다시 시도</button></div>}
    {load === 'ready' && <><p className="schedule-count" role="status">정책 {rows.length}개 · {Math.min(limit, rows.length)}개 표시</p>
      <div className="schedule-grid">{rows.slice(0, limit).map(({ policy, schedule }) => (
        <div key={policy.policy_id} className={'explorer-item' + (picked.includes(policy.policy_id) ? ' picked' : '')}>
          {/* 고르기 체크박스는 카드(펼침 영역) 밖에 둬서 누를 때 카드가 열리고 닫히지 않게 한다 */}
          {picking && <label className="compare-pick">
            <input type="checkbox" checked={picked.includes(policy.policy_id)}
              disabled={!picked.includes(policy.policy_id) && picked.length >= COMPARE_MAX}
              onChange={() => togglePick(policy.policy_id)} />
            비교에 담기
          </label>}
          <PolicyCard p={policy} period={schedule.period} stages={[]} group={group}
            sample={false} onDemoApply={onDemoApply} onAskPolicy={onAsk} onOpenMap={onOpenMap} askAsPrimary />
        </div>
      ))}</div>
      {!rows.length && <div className="schedule-empty">
        {tab === 'saved' && !state?.saved.length ? '관심 있는 정책을 펼쳐 저장해 보세요.' : '선택한 조건에 해당하는 정책이 없어요. 일정 미확인 정책은 전체에서 볼 수 있어요.'}
        {(region || category || filter !== 'all') && (
          <button type="button" className="chip" onClick={() => { setRegion(''); setCategory(''); setFilter('all'); }}>필터 초기화</button>
        )}
      </div>}
      {rows.length > limit && <button className="btn btn-o explorer-more" type="button" onClick={() => setLimit(x => x + PAGE_SIZE)}>정책 {Math.min(PAGE_SIZE, rows.length - limit)}개 더 보기</button>}
    </>}
    {picking && <div className="compare-bar" role="region" aria-label="정책 비교 선택">
      <span role="status"><b>{compareInputs.length}개 선택</b> · {COMPARE_MIN}~{COMPARE_MAX}개 비교 가능{compareInputs.length ? ` · ${compareInputs.map(row => row.policy.policy_name).join(', ')}` : ''}</span>
      <button className="btn btn-p" type="button" disabled={compareInputs.length < COMPARE_MIN} onClick={openCompare}>정책 비교하기</button>
      <button className="btn btn-o" type="button" onClick={stopPicking}>취소</button>
    </div>}
    {tab === 'saved' && missingSaved.length > 0 && <div className="schedule-empty"><b>현재 목록에서 확인하지 못한 관심 정책</b><p>삭제 또는 목록 변경 여부를 확인할 수 없어 이전 일정은 표시하지 않아요.</p>{missingSaved.map(p => <div key={p.policy_id}>{p.policy_name} {safeHttpUrl(p.source_url) && <a href={safeHttpUrl(p.source_url)!} target="_blank" rel="noopener noreferrer">원문 확인</a>} <button type="button" className="chip" onClick={() => state?.toggle(p)}>저장 해제</button></div>)}</div>}
  </div></section>;
}
