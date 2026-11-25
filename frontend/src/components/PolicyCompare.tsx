import { useEffect, useMemo, useRef, useState } from 'react';
import {
  buildComparison, detailFromNode, MISSING_TEXT, orderByDeadline, PERSONAL_UNCHECKED_TEXT,
  type CompareCell, type CompareInput, type ConsultedCheck, type PolicyDetail,
} from '../lib/policyCompare';
import { scheduleView } from '../lib/applicationSchedule';
import { policyQuestion } from '../config/request';
import { mapHref, policyDetailHref } from '../lib/route';
import { useTheme } from '../lib/theme';
import { safeHttpUrl } from '../lib/validate';
import { useScheduleDay } from './ScheduleClock';
import { summarizeConditions } from './TurnView';

const TONE_CLASS = { ok: 'b-ok', warn: 'b-warn', bad: 'b-bad', muted: 'b-muted', plain: '' } as const;
type Ask = (question: string, origin: HTMLElement, policyId?: string) => void;

/** 상담에서 확인한 Checker 결과를 정책 카드와 같은 규칙(summarizeConditions)으로 요약한다. 새로 판정하지 않는다. */
function personalSummary(consulted: ConsultedCheck | undefined) {
  if (!consulted) return null;
  const s = summarizeConditions(consulted.check.conditions);
  const groups = Array.from(s.labelsByBadge, ([badge, { cls, labels }]) => ({ badge, cls, labels: [...labels] }));
  const needs = s.displayedConditions.filter(c => c.status === 'needs_check').map(c => c.label);
  const failed = s.showVerdict && s.displayedConditions.some(c => c.status === 'ineligible');
  const cell: CompareCell = s.unjudged ? { text: '자동 판정 안 함', tone: 'muted' }
    : !groups.length ? { text: '비교한 조건 없음', tone: 'muted' }
    : { text: groups.map(g => `${g.badge}: ${g.labels.join('·')}`).join(' / '), tone: failed || s.excluded ? 'bad' : needs.length || !s.showVerdict ? 'warn' : 'ok' };
  return { ...s, groups, needs: [...new Set(needs)], failed, cell, tags: consulted.tags };
}

/** 정책마다 다음 행동 하나. 확정 표현('신청 가능', '추천')은 쓰지 않는다. */
function nextAction(name: string, source: string | null, personal: ReturnType<typeof personalSummary>) {
  if (!personal) return { label: '내 조건으로 확인하기', question: policyQuestion(name) };
  if ((personal.failed || personal.excluded) && source) return { label: '공식 자격기준 확인 ↗', href: source };
  if (personal.needs.length) return { label: `${personal.needs[0]} 조건 확인하기`, question: `${name}의 ${personal.needs[0]} 조건을 내 조건으로 확인해 주세요.` };
  return { label: '신청 방법 물어보기', question: `${name} 신청 방법과 필요 서류를 알려 주세요.` };
}

/**
 * 관심 정책·둘러보기에서 고른 정책 2~3개를 SSOK 안에서 비교한다.
 * 비교 요약 → 정책별 요약 카드(다음 행동) → 자세히 비교하기(접힘) 순서로 보여 준다.
 * 값은 목록 데이터·배포 공고 요약·이미 받은 상담의 Checker 결과만 쓴다. 판정·추천 순위를 새로 만들지 않는다.
 */
export function PolicyCompare({ inputs, consulted, onBack, onAsk, onOpenMap, initialDetails }: {
  inputs: CompareInput[];
  consulted?: Map<string, ConsultedCheck>;
  onBack: () => void;
  onAsk: Ask;
  onOpenMap?: () => void;
  /** 테스트·미리보기용. 없으면 배포 스냅샷에서 읽는다 */
  initialDetails?: Map<string, PolicyDetail>;
}) {
  const today = useScheduleDay(), theme = useTheme();
  const [details,setDetails] = useState<Map<string, PolicyDetail>>(initialDetails ?? new Map());
  const [detailState, setDetailState] = useState<'loading' | 'ready' | 'error'>(initialDetails ? 'ready' : 'loading');
  const [differencesOnly, setDifferencesOnly] = useState(true);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [openId, setOpenId] = useState<string | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => { titleRef.current?.focus(); }, []);
  useEffect(() => {
    if (initialDetails) return;
    let alive = true;
    import('../graph/loadGraph').then(({ loadGraphDataset }) => loadGraphDataset()).then(data => {
      if (!alive) return;
      setDetails(new Map(inputs.map(({ policy }) => [policy.policy_id, detailFromNode(data.index.nodes.get(policy.policy_id)?.data)])));
      setDetailState('ready');
    }).catch(() => { if (alive) setDetailState('error'); });
    return () => { alive = false; };
  }, [inputs, initialDetails]);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (openId && !dialog.open) dialog.showModal?.();
    if (!openId && dialog.open) dialog.close();
  }, [openId]);

  const ordered = useMemo(() => orderByDeadline(inputs, today), [inputs, today]);
  const personal = useMemo(() => new Map(ordered.map(({ policy }) => [policy.policy_id, personalSummary(consulted?.get(policy.policy_id))])), [ordered, consulted]);
  const personalCells = new Map([...personal].flatMap(([id, p]) => (p ? [[id, p.cell] as const] : [])));
  const { rows, counts, checklists, views } = buildComparison(ordered, details, today, personalCells);
  // '내 조건'은 모두 같아도 놓치면 안 되는 행이라 핵심 차이에서도 남긴다
  const shownRows = differencesOnly ? rows.filter(row => row.differs || row.key === 'personal') : rows;
  const folded = rows.length - shownRows.length;
  const opened = ordered.find(({ policy }) => policy.policy_id === openId);
  const ask: Ask = (question, origin, policyId) => { setOpenId(null); onAsk(question, origin, policyId); };
  const toggle = (id: string) => setChecked(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div className="compare-view">
      <div className="compare-head">
        <button className="btn btn-o" type="button" onClick={onBack}>← 목록으로</button>
        <span className="schedule-count">관심 정책 비교 · {inputs.length}개</span>
      </div>
      <h3 className="compare-title" ref={titleRef} tabIndex={-1}>어떤 정책부터 확인할까요?</h3>
      <p className="schedule-scope">마감이 가까운 순으로 놓았어요(추천 순서가 아니에요). 내 조건은 상담에서 확인한 정책만 그 결과를 보여 드리고, 최종 신청 자격은 공식 공고에서 확인해 주세요.</p>
      <ul className="compare-stats" aria-label="비교 요약">
        <li><span>비교 중</span><b>{inputs.length}개</b></li>
        <li className="ok"><span>접수 중</span><b>{counts.open}개</b></li>
        <li className="warn"><span>7일 이내 마감</span><b>{counts.soon}개</b></li>
        <li><span>일정 확인 필요</span><b>{counts.unknown}개</b></li>
        <li className={counts.consulted < inputs.length ? 'warn' : 'ok'}><span>내 조건 확인</span><b>{counts.consulted}/{inputs.length}개</b></li>
      </ul>

      <ol className="compare-cards" aria-label="정책별 요약">
        {ordered.map(({ policy }, i) => {
          const view = views[i], p = personal.get(policy.policy_id) ?? null, detail = details.get(policy.policy_id) ?? {};
          const source = safeHttpUrl(policy.source_url);
          const detailHref = policyDetailHref(policy.policy_id, undefined, theme);
          const action = nextAction(policy.policy_name, source, p);
          return (
            <li key={policy.policy_id} className="compare-card">
              <div className="compare-card-top">
                <h4>{policy.policy_name}</h4>
                {policy.agency && <small>{policy.agency}</small>}
                <div className="compare-badges">
                  <span className={'badge ' + (view.status === 'open' ? 'b-ok' : view.status === 'closed' ? 'b-bad' : 'b-muted')}>{view.label}</span>
                  {view.badge && <span className="schedule-countdown">{view.badge}</span>}
                </div>
              </div>
              <div className="compare-block">
                <span className="compare-k">지원 내용</span>
                <p className={'compare-benefit' + (detail.benefit ? '' : ' missing')}>
                  {detail.benefit || (detailState === 'loading' ? '불러오는 중…' : MISSING_TEXT)}
                </p>
              </div>
              <div className="compare-block">
                <span className="compare-k">내 조건</span>
                {!p ? <p className="compare-muted">{PERSONAL_UNCHECKED_TEXT} · 상담에서 이 정책을 선택해 확인할 수 있어요.</p> : <>
                  <small className="compare-muted">최근 상담 기준 · 입력: {p.tags.join(' · ') || '조건 미입력'}</small>
                  {p.unjudged ? <p className="compare-muted">이 정책은 자동 판정하지 않았어요. 자격 요건은 공식 원문에서 확인해 주세요.</p>
                    : !p.groups.length ? <p className="compare-muted">상담에서 비교한 조건이 없어요.</p>
                    : <ul className="compare-conds">{p.groups.map(g => <li key={g.badge}><span className={'badge ' + g.cls}>{g.badge}</span> {g.labels.join(' · ')}</li>)}</ul>}
                  {p.hidesVerdict && <p className="compare-muted">조건이 어떻게 결합되는지 확인되지 않아 개별 비교 결과만 보여 드려요.</p>}
                  {p.excluded && <p className="exclusion-warn">지원 제외 대상 조건에 해당할 수 있어요. 공식 원문을 확인해 주세요.</p>}
                </>}
              </div>
              <div className="compare-block">
                <span className="compare-k">추가로 확인할 것</span>
                <ul className="compare-checklist">
                  {checklists[i].map(item => {
                    const id = `${policy.policy_id}:${item.key}`;
                    return <li key={item.key}><label><input type="checkbox" checked={checked.has(id)} onChange={() => toggle(id)} /> {item.label}</label></li>;
                  })}
                </ul>
              </div>
              <div className="compare-actions">
                {'href' in action
                  ? <a className="btn btn-p" href={action.href} target="_blank" rel="noopener noreferrer">{action.label}</a>
                  : <button className="btn btn-p" type="button" onClick={(event) => ask(action.question, event.currentTarget, policy.policy_id)}>{action.label}</button>}
                <div className="compare-actions-sub">
                  <button className="btn btn-o" type="button" onClick={() => setOpenId(policy.policy_id)}>빠르게 보기</button>
                  {/* 다른 카드와 같은 Django 정책 상세(SSR). 새 탭으로 열어 비교 화면과 체크 표시를 지킨다 */}
                  {detailHref && <a className="btn btn-o" href={detailHref} target="_blank" rel="noopener noreferrer"
                    aria-label={`${policy.policy_name} 정책 상세보기 (새 탭)`}>정책 상세보기 ↗</a>}
                  {source &&<a className="btn btn-o" href={source} target="_blank" rel="noopener noreferrer">공식 공고 ↗</a>}
                </div>
              </div>
            </li>
          );
        })}
      </ol>
      <p className="schedule-count">체크는 확인한 항목을 표시하는 메모예요(새로 고치면 지워져요). ‘{MISSING_TEXT}’은 목록 데이터에 값이 없다는 뜻이고, 조건이 없다는 뜻은 아니에요.</p>

      <details className="compare-more">
        <summary>자세히 비교하기 <small>항목별로 나란히 보기</small></summary>
        <div className="schedule-tools" role="group" aria-label="비교표 보기 방식">
          <button className="chip" type="button" aria-pressed={differencesOnly} onClick={() => setDifferencesOnly(true)}>핵심 차이</button>
          <button className="chip" type="button" aria-pressed={!differencesOnly} onClick={() => setDifferencesOnly(false)}>전체 항목</button>
          <span className="schedule-count" role="status">{differencesOnly && folded > 0 ? `같은 항목 ${folded}개 접음` : ''}</span>
        </div>
        {detailState === 'error' && <p className="schedule-count" role="status">공고 요약을 불러오지 못해 지원 내용·대상은 공식 원문에서 확인해 주세요.</p>}
        <div className="compare-scroll" role="region" aria-label="정책 비교표" tabIndex={0}>
          <table className={'compare-table' + (ordered.length === 3 ? ' three' : '')}>
            <thead>
              <tr>
                <th scope="col">비교 항목</th>
                {ordered.map(({ policy }) => <th scope="col" key={policy.policy_id}>{policy.policy_name}</th>)}
              </tr>
            </thead>
            <tbody>
              {shownRows.map(row => (
                <tr key={row.key}>
                  <th scope="row">{row.label}</th>
                  {row.cells.map((cell, i) => (
                    <td key={ordered[i].policy.policy_id}>
                      {cell.tone === 'plain' ? <span className="compare-text">{cell.text}</span> : <span className={'badge ' + TONE_CLASS[cell.tone]}>{cell.text}</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>

      <dialog ref={dialogRef} className="compare-dialog" aria-labelledby="compare-dialog-title" onClose={() => setOpenId(null)}>
        {/* 상담·관계 지도로 넘어가면 팝업을 먼저 닫는다 */}
        {opened && <PolicyDetailBody input={opened} detail={details.get(opened.policy.policy_id) ?? {}} today={today}
          personal={personal.get(opened.policy.policy_id)?.cell ?? null}
          onAsk={ask} onOpenMap={() => { setOpenId(null); onOpenMap?.(); }} />}
        <form method="dialog"><button className="btn btn-o" type="submit">닫기</button></form>
      </dialog>
    </div>
  );
}

function PolicyDetailBody({ input: { policy, regions }, detail, today, personal, onAsk, onOpenMap }: {
  input: CompareInput; detail: PolicyDetail; today: string; personal: CompareCell | null;
  onAsk: Ask; onOpenMap?: () => void;
}) {
  const view = scheduleView(policy, today);
  const source = safeHttpUrl(policy.source_url);
  const facts: [string, string][] = [
    ['접수 상태', view.badge ? `${view.label} · ${view.badge}` : view.label],
    ['신청 기간', view.period && view.period !== view.label ? view.period : MISSING_TEXT],
    ['담당 기관', policy.agency || MISSING_TEXT],
    ['지역', regions.join(' · ') || MISSING_TEXT],
    ['지원 내용', detail.benefit || MISSING_TEXT],
    ['지원 대상 (공고 기준)', detail.eligibility || MISSING_TEXT],
    ['신청 방법', detail.application_method || MISSING_TEXT],
    ['내 조건', personal ? `최근 상담 기준 · ${personal.text}` : `${PERSONAL_UNCHECKED_TEXT} · 상담에서 확인해 주세요.`],
  ];
  return (
    <>
      <h3 id="compare-dialog-title">{policy.policy_name}</h3>
      <dl className="compare-facts">{facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      <div className="compare-actions-sub">
        <button className="btn btn-p" type="button" onClick={(event) => onAsk(policyQuestion(policy.policy_name), event.currentTarget, policy.policy_id)}>이 정책 상담하기</button>
        <a className="btn btn-o" href={mapHref(policy.policy_id)} onClick={(event) => {
          if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) onOpenMap?.();
        }}>관계 지도</a>
        {source && <a className="btn btn-o" href={source} target="_blank" rel="noopener noreferrer">공식 공고 ↗</a>}
      </div>
    </>
  );
}
