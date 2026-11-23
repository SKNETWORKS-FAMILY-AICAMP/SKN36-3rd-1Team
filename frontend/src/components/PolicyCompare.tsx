import { useEffect, useRef, useState } from 'react';
import { buildComparison, detailFromNode, PERSONAL_CHECK_TEXT, type CompareInput, type PolicyDetail } from '../lib/policyCompare';
import { scheduleView } from '../lib/applicationSchedule';
import { policyQuestion } from '../config/request';
import { mapHref } from '../lib/route';
import { safeHttpUrl } from '../lib/validate';
import { useScheduleDay } from './ScheduleClock';

const TONE_CLASS = { ok: 'b-ok', warn: 'b-warn', bad: 'b-bad', muted: 'b-muted', plain: '' } as const;

/**
 * 관심 정책·둘러보기에서 고른 정책 2~3개를 SSOK 안에서 나란히 비교한다.
 * 값은 목록 API(또는 배포 스냅샷)와 배포 정책 노드의 공고 요약만 쓴다. 개인 자격은 판정하지 않는다.
 */
export function PolicyCompare({ inputs, onBack, onAsk, onOpenMap, initialDetails }: {
  inputs: CompareInput[];
  onBack: () => void;
  onAsk: (question: string, origin: HTMLElement, policyId?: string) => void;
  onOpenMap?: () => void;
  /** 테스트·미리보기용. 없으면 배포 스냅샷에서 읽는다 */
  initialDetails?: Map<string, PolicyDetail>;
}) {
  const today = useScheduleDay();
  const [details, setDetails] = useState<Map<string, PolicyDetail>>(initialDetails ?? new Map());
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

  const { rows, counts, checklists } = buildComparison(inputs, details, today);
  // '내 조건 판정'은 항상 같아도(모두 추가 확인 필요) 놓치면 안 되는 안내라 차이점 보기에서도 남긴다
  const shownRows = differencesOnly ? rows.filter(row => row.differs || row.key === 'personal') : rows;
  const folded = rows.length - shownRows.length;
  const opened = inputs.find(({ policy }) => policy.policy_id === openId);
  const toggle = (id: string) => setChecked(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });

  return (
    <div className="compare-view">
      <div className="compare-head">
        <button className="btn btn-o" type="button" onClick={onBack}>← 목록으로</button>
        <h3 ref={titleRef} tabIndex={-1}>정책 비교 <span>{inputs.length}개</span></h3>
      </div>
      <p className="schedule-scope">고른 정책의 목록 데이터와 공고 요약을 나란히 보여 드려요. 내 신청 자격은 판정하지 않아요. 내 조건은 정책별 ‘내 조건으로 확인하기’로 상담에서 확인해 주세요.</p>
      <ul className="compare-summary" aria-label="비교 요약">
        <li>비교 정책 {inputs.length}개</li>
        <li className="ok">접수 중 {counts.open}개</li>
        <li className="warn">7일 이내 마감 {counts.soon}개</li>
        <li className="muted">일정 확인 필요 {counts.unknown}개</li>
        <li className="warn">내 조건 판정 · 모두 {PERSONAL_CHECK_TEXT}</li>
      </ul>
      <div className="schedule-tools" role="group" aria-label="비교표 보기 방식">
        <button className="chip" type="button" aria-pressed={differencesOnly} onClick={() => setDifferencesOnly(true)}>차이점만 보기</button>
        <button className="chip" type="button" aria-pressed={!differencesOnly} onClick={() => setDifferencesOnly(false)}>전체 항목 보기</button>
        <span className="schedule-count" role="status">{differencesOnly && folded > 0 ? `같은 항목 ${folded}개 접음` : ''}</span>
      </div>
      {detailState === 'loading' && <p className="schedule-count" role="status">공고 요약(지원 내용·대상)을 불러오고 있어요…</p>}
      {detailState === 'error' && <p className="schedule-count" role="status">공고 요약을 불러오지 못해 지원 내용·대상은 공식 원문에서 확인해 주세요.</p>}
      <div className="compare-scroll" role="region" aria-label="정책 비교표" tabIndex={0}>
        <table className={'compare-table' + (inputs.length === 3 ? ' three' : '')}>
          <thead>
            <tr>
              <th scope="col">비교 항목</th>
              {inputs.map(({ policy }) => <th scope="col" key={policy.policy_id}>{policy.policy_name}</th>)}
            </tr>
          </thead>
          <tbody>
            {shownRows.map(row => (
              <tr key={row.key} className={differencesOnly ? 'different' : undefined}>
                <th scope="row">{row.label}</th>
                {row.cells.map((cell, i) => (
                  <td key={inputs[i].policy.policy_id}>
                    {cell.tone === 'plain' ? <span className="compare-text">{cell.text}</span> : <span className={'badge ' + TONE_CLASS[cell.tone]}>{cell.text}</span>}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="check-row">
              <th scope="row">다음 확인</th>
              {inputs.map(({ policy }, i) => (
                <td key={policy.policy_id}>
                  <ul className="compare-checklist">
                    {checklists[i].map(item => {
                      const id = `${policy.policy_id}:${item.key}`;
                      return <li key={item.key}><label><input type="checkbox" checked={checked.has(id)} onChange={() => toggle(id)} /> {item.label}</label></li>;
                    })}
                  </ul>
                </td>
              ))}
            </tr>
            <tr>
              <th scope="row">정책 정보</th>
              {inputs.map(({ policy }) => (
                <td key={policy.policy_id}>
                  <div className="compare-actions">
                    <button className="btn btn-p" type="button" onClick={(event) => onAsk(policyQuestion(policy.policy_name), event.currentTarget, policy.policy_id)}>내 조건으로 확인하기</button>
                    <button className="btn btn-o" type="button" onClick={() => setOpenId(policy.policy_id)}>상세 보기</button>
                  </div>
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
      <p className="schedule-count">체크는 확인한 항목을 표시하는 메모예요. 화면을 새로 고치면 지워져요.</p>

      <dialog ref={dialogRef} className="compare-dialog" aria-labelledby="compare-dialog-title" onClose={() => setOpenId(null)}>
        {/* 상담·관계 지도로 넘어가면 팝업을 먼저 닫는다 */}
        {opened && <PolicyDetailBody input={opened} detail={details.get(opened.policy.policy_id) ?? {}} today={today}
          onAsk={(question, origin, policyId) => { setOpenId(null); onAsk(question, origin, policyId); }}
          onOpenMap={() => { setOpenId(null); onOpenMap?.(); }} />}
        <form method="dialog"><button className="btn btn-o" type="submit">닫기</button></form>
      </dialog>
    </div>
  );
}

function PolicyDetailBody({ input: { policy, regions }, detail, today, onAsk, onOpenMap }: {
  input: CompareInput; detail: PolicyDetail; today: string;
  onAsk: (question: string, origin: HTMLElement, policyId?: string) => void; onOpenMap?: () => void;
}) {
  const view = scheduleView(policy, today);
  const source = safeHttpUrl(policy.source_url);
  const facts: [string, string][] = [
    ['접수 상태', view.badge ? `${view.label} · ${view.badge}` : view.label],
    ['신청 기간', view.period && view.period !== view.label ? view.period : '공식 원문에서 확인해 주세요.'],
    ['담당 기관', policy.agency || '공식 원문에서 확인해 주세요.'],
    ['지역', regions.join(' · ') || '공식 원문에서 확인해 주세요.'],
    ['지원 내용', detail.benefit || '공식 원문에서 확인해 주세요.'],
    ['지원 대상 (공고 기준)', detail.eligibility || '공식 원문에서 확인해 주세요.'],
    ['신청 방법', detail.application_method || '공식 원문에서 확인해 주세요.'],
    ['내 조건 판정', `${PERSONAL_CHECK_TEXT} · 상담에서 내 조건으로 확인해 주세요.`],
  ];
  return (
    <>
      <h3 id="compare-dialog-title">{policy.policy_name}</h3>
      <dl className="compare-facts">{facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      <div className="compare-actions">
        <button className="btn btn-p" type="button" onClick={(event) => onAsk(policyQuestion(policy.policy_name), event.currentTarget, policy.policy_id)}>내 조건으로 확인하기</button>
        <a className="btn btn-o" href={mapHref(policy.policy_id)} onClick={(event) => {
          if (!event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey) onOpenMap?.();
        }}>관계 지도</a>
        {source && <a className="btn btn-o" href={source} target="_blank" rel="noopener noreferrer">원문 보기 ↗</a>}
      </div>
    </>
  );
}
