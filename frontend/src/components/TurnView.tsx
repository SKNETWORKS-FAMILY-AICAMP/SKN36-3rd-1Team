import { useEffect, useId, useState, type ReactElement, type ReactNode } from 'react';
import type { AskResponse, ConditionResult, ConditionStatus, FollowUp, PolicyCheck, ReceptionStatus } from '../api/types';
import { hasOnlyAndGroups, isGroupRow, isUnjudgedOnly, matchesExclusion } from '../lib/conditionGroups';
import { Icon, type IconName } from './Icon';
import { MascotFace } from './Logo';
import { safeHttpUrl } from '../lib/validate';
import { lifeHref, mapHref } from '../lib/route';
import { answerView, freshnessNote, sourceDetail } from '../lib/answerView';
import { LOADING_STAGES } from '../config/request';
import type { GraphIndex } from '../graph/graphModel';
import { policyLifeStages } from '../graph/lifeStages';

export type Turn =
  | { id: number; kind: 'qa'; question: string; tags: { icon: IconName; text: string }[]; state: 'loading' | 'done' | 'error'; response?: AskResponse; error?: string }
  | { id: number; kind: 'prompt'; text: string };

const CONDITION_LABEL: Record<ConditionStatus, [string, string]> = {
  eligible: ['b-ok', '조건상 적합'],
  needs_check: ['b-warn', '추가 확인 필요'],
  ineligible: ['b-bad', '조건 불일치'],
};
const RECEPTION_LABEL: Record<ReceptionStatus, [string, string]> = {
  before: ['b-muted', '접수 전'], open: ['b-ok', '접수 중'], closed: ['b-bad', '마감'], unknown: ['b-muted', '모집 상태 미확인'],
};

interface Props {
  turn: Turn;
  onFollowUp: (followUp: FollowUp, origin: HTMLElement) => void;
  onRetry: (id: number) => void;
  onDemoApply: () => void;
  /** 예시 응답 모드(백엔드 주소 없음)인지. 응답이 오기 전 표시와 시연 안내에 쓴다. */
  sampleMode: boolean;
}

export function TurnView({ turn, onFollowUp, onRetry, onDemoApply, sampleMode }: Props) {
  if (turn.kind === 'prompt') {
    return (
      <div className="turn">
        <div className="acard">
          <BotHead sub="조건 확인" />
          <div className="abody"><AnswerText text={turn.text} /></div>
        </div>
      </div>
    );
  }

  const r = turn.response;
  return (
    <div className="turn">
      <div className="qrow"><div className="qb">{turn.question}</div></div>
      {turn.tags.length > 0 && (
        <div className="qtags">{turn.tags.map((t) => <span key={t.text}><Icon name={t.icon} className="ic" />{t.text}</span>)}</div>
      )}
      <div className={'acard' + (turn.state === 'loading' ? ' thinking' : '')}>
        <BotHead sub="공식 원문 기반 답변" sample={r ? r.is_sample : sampleMode} />
        <div className="abody">
          {turn.state === 'loading' && <Loading />}
          {turn.state === 'error' && (
            <>
              <NoticeBox kind="bad" icon="warning" title="답변을 불러오지 못했어요"
                text={turn.error ?? '잠시 후 다시 시도해 주세요.'} />
              <button type="button" className="chip stag" style={{ marginTop: 12, animationDelay: '.12s' }} onClick={() => onRetry(turn.id)}>다시 시도</button>
            </>
          )}
          {turn.state === 'done' && r && <Answer r={r} onFollowUp={onFollowUp} onDemoApply={onDemoApply} />}
        </div>
      </div>
    </div>
  );
}

function BotHead({ sub, sample }: { sub: string; sample?: boolean }) {
  return (
    <div className="ahead">
      <span className="bot"><MascotFace /></span>
      <div><b>SSOK</b><small>{sub}</small></div>
      {sample && <span className="badge b-warn">예시 응답</span>}
    </div>
  );
}

/** 기다린 시간에 따라 안내 문구를 바꾼다 (config/request의 LOADING_STAGES). */
function Loading() {
  const [stage, setStage] = useState(0);
  useEffect(() => {
    const timers = LOADING_STAGES.slice(1).map((s, i) => window.setTimeout(() => setStage(i + 1), s.after));
    return () => timers.forEach((t) => clearTimeout(t));
  }, []);
  return (
    <div className="loading" role="status">
      <div className="lt">{LOADING_STAGES[stage].text}<i /><i /><i /></div>
      {[92, 78, 60].map((w) => <div key={w} className="sk" style={{ width: `${w}%` }} />)}
    </div>
  );
}

/** 본문 속 [1] 표시를 출처 번호 위첨자로 바꾼다 (HTML을 넣지 않고 텍스트로만 그린다). */
function AnswerText({ text, sources }: { text: string; sources?: AskResponse['citations'] }) {
  return (
    <div className="answer">
      {text.split(/\n\s*\n/).filter((paragraph) => paragraph.trim()).map((paragraph, index) => (
        <p key={index}>
          {paragraph.split(/((?<!\*)\*\*(?!\*)[^*\r\n]+\*\*(?!\*))/).map((segment, index) => {
            const bold = index % 2 === 1;
            const content = (bold ? segment.slice(2, -2) : segment).split(/((?:\[\d+\])+)/).map((part, i) => {
            const citations = /^\[\d+\]/.test(part) ? part.match(/\d+/g) : null;
            if (!citations) return <span key={i}>{part}</span>;
            return (
              <sup key={i}>
                {citations.map((number, position) => {
                  const source = sources?.[Number(number) - 1];
                  const href = source ? safeHttpUrl(source.url) : null;
                  return (
                    <span key={position}>
                      {position > 0 && ', '}
                      {href && source ? (
                        <a href={href} target="_blank" rel="noopener noreferrer"
                          aria-label={`출처 ${number} 원문 보기 · ${source.title} (새 탭)`}>{number}</a>
                      ) : number}
                    </span>
                  );
                })}
              </sup>
            );
            });
            return bold ? <strong key={index}>{content}</strong> : <span key={index}>{content}</span>;
          })}
        </p>
      ))}
    </div>
  );
}

function Answer({ r, onFollowUp, onDemoApply }: { r: AskResponse; onFollowUp: Props['onFollowUp']; onDemoApply: () => void }) {
  const policyGroup = useId();
  const [policyIndex, setPolicyIndex] = useState<GraphIndex | null>(null);
  const hasLivePolicies = !r.is_sample && r.policy_checks.length > 0;
  useEffect(() => {
    if (!hasLivePolicies) return;
    let active = true;
    import('../graph/loadGraph').then(({ loadGraphDataset }) => loadGraphDataset())
      .then(({ index }) => { if (active) setPolicyIndex(index); })
      .catch(() => { /* 신청 기간을 확인할 수 없으면 카드에서 안내한다. */ });
    return () => { active = false; };
  }, [hasLivePolicies]);
  const view = answerView(r);
  const infoRequests = new Map<string, FollowUp>();
  const questions: FollowUp[] = [];
  for (const item of r.follow_up_questions) {
    if (item.kind === 'question') {
      questions.push(item);
    } else if (item.field && !infoRequests.has(item.field)) {
      infoRequests.set(item.field, item);
    }
  }
  const blocks: ReactElement[] = [];
  if (infoRequests.size > 0) {
    blocks.push(
      <Block key="info-requests" title="추가로 확인할 정보" icon="bulb">
        <div className="chips">
          {[...infoRequests].map(([field, item]) => (
            <button key={field} type="button" className="chip" onClick={(event) => onFollowUp(item, event.currentTarget)}>
              {item.text}
            </button>
          ))}
        </div>
      </Block>,
    );
  }
  if (view.answerText) blocks.push(<AnswerText key="a" text={view.answerText} sources={r.citations} />);
  if (r.status === 'info' && view.guide) {
    blocks.push(
      <NoticeBox key="g" kind={view.warning ? 'warn' : 'info'}
        icon={view.warning ? 'search' : 'info'} title={view.guide.title}
        text={view.warning?.text ?? '공식 원문에서 추가로 확인해 주세요.'} />,
    );
    if (r.answer.trim()) {
      blocks.push(
        <AnswerText key="info-answer" text={r.answer} sources={r.citations} />,
      );
    }
  } else if (view.guide) {
    blocks.push(
      <NoticeBox key="g" kind={view.warning ? 'warn' : view.guide.kind}
        icon={view.warning || view.guide.kind === 'warn' ? 'search' : 'info'}
        title={view.guide.title} text={[view.guide.text, view.warning?.text].filter(Boolean).join('\n\n')} />,
    );
  } else if (view.warning) {
    blocks.push(<NoticeBox key="w" kind="warn" icon="search" title={view.warning.title} text={view.warning.text} />);
  }
  // 확인할 항목만 모은다. 개별 적합·불일치는 그룹 맥락 없이 정책 자격으로 읽힐 수 있어 여기에 올리지 않는다.
  const policyReasons = r.policy_checks
    .map((policy) => ({ policy, items: checkItems(policy.conditions, infoRequests), excluded: matchesExclusion(policy.conditions) }))
    .filter(({ items, excluded }) => items.length > 0 || excluded);
  if (policyReasons.length > 0) {
    blocks.push(
      <Block key="policy-reasons" title="정책별로 확인할 조건" icon="check">
        <ul className="check-items">
          {policyReasons.map(({ policy, items, excluded }) => (
            <li key={policy.policy_id}>
              <b>{policy.policy_name}</b>
              {excluded && <p className="exclusion-warn">{EXCLUSION_NOTICE}</p>}
              <ul>
                {items.map(({ label, details }) => (
                  <li key={label}>
                    <span className="k">{label}</span>
                    <span className="why">
                      {details.slice(0, VISIBLE_DETAILS).map((detail) => <span key={detail} className="detail">{detail}</span>)}
                      {details.length > VISIBLE_DETAILS && (
                        <details>
                          <summary>외 {details.length - VISIBLE_DETAILS}개 보기</summary>
                          <ul>{details.slice(VISIBLE_DETAILS).map((detail) => <li key={detail}>{detail}</li>)}</ul>
                        </details>
                      )}
                    </span>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </Block>,
    );
  }
  if (r.policy_checks.length > 0) {
    blocks.push(
      <Block key="policies" title={`관련 정책 ${r.policy_checks.length}개`} icon="check" tag={r.is_sample ? '시연용 가상 데이터' : undefined}>
        <div className="policy-list">
          {policyRows(r.policy_checks, r.is_sample ? null : policyIndex).map(({ policy, period }) => (
            <PolicyCard key={policy.policy_id} p={policy} period={period}
              stages={!r.is_sample && policyIndex ? policyLifeStages(policyIndex, policy.policy_id) : []}
              group={policyGroup} sample={r.is_sample} onDemoApply={onDemoApply} />
          ))}
        </div>
      </Block>,
    );
  }
  if (view.extraInfo) {
    blocks.push(<NoticeBox key="i" kind="info" icon="info" title={view.extraInfo.title} text={view.extraInfo.text} />);
  }
  if (r.citations.length > 0) {
    const sources = new Map<string | number, {
      href: ReturnType<typeof safeHttpUrl>;
      title: string;
      details: Set<string>;
    }>();
    r.citations.forEach((citation, index) => {
      const href = safeHttpUrl(citation.url);
      const key = href ?? index;
      const detail = sourceDetail(href, citation.page_number);
      const source = sources.get(key);
      if (source) {
        source.details.add(detail);
      } else {
        sources.set(key, { href, title: citation.title, details: new Set([detail]) });
      }
    });
    blocks.push(
      <Block key="src" title="근거 출처" icon="books">
        <div className="srcs">
          {[...sources].map(([key, { href, title, details }]) => {
            const inner = <><span><b>{title}</b><span>{[...details].join(' · ')}</span></span>{href && <span className="arr">↗</span>}</>;
            // 안전하지 않은 주소는 링크로 만들지 않고 제목만 보여 준다
            return href
              ? <a key={key} className="src" href={href} target="_blank" rel="noopener noreferrer">{inner}</a>
              : <div key={key} className="src">{inner}</div>;
          })}
        </div>
        <p className="fresh">{freshnessNote(r.checked_at, r.is_sample)}</p>
      </Block>,
    );
  }
  if (questions.length > 0) {
    blocks.push(
      <Block key="f" title="이것도 알려 주시면 더 정확해요" icon="bulb">
        <div className="chips">
          {questions.map((f, i) => <button key={`${i}-${f.text}`} type="button" className="chip" onClick={(e) => onFollowUp(f, e.currentTarget)}>{f.text}</button>)}
        </div>
      </Block>,
    );
  }
  return <>{blocks.map((b, i) => <div key={b.key} className="stag" style={{ animationDelay: `${i * 0.12}s` }}>{b}</div>)}</>;
}

function Block({ title, icon, tag, children }: { title: string; icon: IconName; tag?: string; children: ReactNode }) {
  return (
    <div className="blk">
      <h3><Icon name={icon} className="ic" />{title}{tag && <span className="tag">{tag}</span>}</h3>
      {children}
    </div>
  );
}

/**
 * 정책 카드의 생애단계 칩. 누르면 생애주기 화면의 그 단계로 간다.
 * 그래프 데이터는 필요할 때만 불러온다(상담 화면 첫 용량에 넣지 않음). 못 읽거나 연결된 단계가 없으면 아무것도 그리지 않는다.
 */
function StageChips({ stages }: { stages: string[] }) {
  if (stages.length === 0) return null;
  return (
    <div className="pstages" aria-label="생애단계">
      {stages.map((name) => <a key={name} className="stage-chip" href={lifeHref(name)} title={`생애주기에서 '${name}' 정책 보기`}>{name}</a>)}
    </div>
  );
}

function policyRows(policies: PolicyCheck[], index: GraphIndex | null) {
  return policies.map((policy) => {
    const data = index?.nodes.get(policy.policy_id)?.data;
    const text = (value: unknown) => typeof value === 'string' ? value.trim() : '';
    const start = text(data?.application_start);
    const end = text(data?.application_end);
    const deadline = policy.reception_status === 'open' && /^\d{4}-\d{2}-\d{2}$/.test(end) ? end : '';
    const period = start && end ? `${start} ~ ${end}`
      : end ? `종료일 ${end}`
      : start ? `시작일 ${start}${data?.application_is_ongoing === true ? ' · 상시 접수' : ''}`
      : text(data?.application_period);
    return { policy, deadline, period };
  }).sort((a, b) => {
    const closedOrder = Number(a.policy.reception_status === 'closed') - Number(b.policy.reception_status === 'closed');
    if (closedOrder) return closedOrder;
    if (!a.deadline || !b.deadline) return Number(!a.deadline) - Number(!b.deadline);
    return a.deadline.localeCompare(b.deadline);
  });
}

const OFFICIAL_ELIGIBILITY_NOTICE = '신청 자격은 공식 공고에서 확인해 주세요.';
/** 제외(NOT) 그룹에 입력 정보가 해당할 때(lib/conditionGroups `matchesExclusion`). 어떤 조건인지는 원문에서 확인하게 한다. */
const EXCLUSION_NOTICE = '입력하신 정보가 이 정책의 지원 제외 대상 조건에 해당해요. 공식 원문의 제외 대상을 확인해 주세요.';
/** Checker가 입력이 없어 보류할 때 쓰는 사유. 위쪽 입력 버튼과 같은 요청이라 요약에서 반복하지 않는다. */
const MISSING_INPUT_REASON: Record<string, string> = { age: '사용자의 만 나이가 입력되지 않았습니다.' };
/** 상단 요약에서 항목 하나에 바로 보여 줄 사유 수. 나머지는 '외 N개 보기'로 접는다. */
const VISIBLE_DETAILS = 2;

/** 정책 하나의 추가 확인 사유를 항목(label)별로 모은다. 일반 공고 안내·입력 요청·중복 사유는 뺀다. */
function checkItems(conditions: ConditionResult[], infoRequests: Map<string, FollowUp>) {
  const byLabel = new Map<string, Set<string>>();
  for (const condition of conditions) {
    if (condition.status !== 'needs_check' || isGroupRow(condition)) continue;
    if (infoRequests.has(condition.field) && condition.reason === MISSING_INPUT_REASON[condition.field]) continue;
    const detail = conditionReasonForDisplay(condition.reason);
    if (detail === OFFICIAL_ELIGIBILITY_NOTICE) continue;
    const details = byLabel.get(condition.label) ?? new Set<string>();
    details.add(detail);
    byLabel.set(condition.label, details);
  }
  return Array.from(byLabel, ([label, details]) => ({ label, details: [...details] }));
}

/** 그룹 구조가 AND로만 확인된 정책만 개별 적합·불일치를 배지로 보여 준다(lib/conditionGroups). */
function conditionBadge(status: ConditionStatus, showVerdict: boolean): [string, string] {
  return showVerdict || status === 'needs_check' ? CONDITION_LABEL[status] : ['b-muted', '개별 비교'];
}

function conditionReasonForDisplay(reason: string) {
  if (reason === MISSING_INPUT_REASON.age) return '만 나이를 알려 주세요.';
  const detail = reason
    .replace('UNKNOWN 조건 그룹은 자동 판정할 수 없습니다.', '')
    .replace('조건 그룹의 내부 구조가 올바르지 않습니다.', '')
    .replace('데이터 품질 경고로 해당 자격 요건의 확인이 필요합니다.', '')
    .replace('데이터 품질 경고로 자동 판정을 보류합니다.', '')
    .trim();

  return detail === reason ? reason : [detail, OFFICIAL_ELIGIBILITY_NOTICE].filter(Boolean).join(' ');
}

function PolicyCard({ p, group, period, stages, sample, onDemoApply }: {
  p: PolicyCheck; group: string; period: string; stages: string[]; sample: boolean; onDemoApply: () => void;
}) {
  const unjudged = isUnjudgedOnly(p.conditions);
  const incomeNotices = new Set<string>();
  // 그룹 종합 결과 행(…해야 합니다(종합 결과))은 계산 과정이라 화면에서 뺀다.
  const displayedConditions = p.conditions.filter((condition) => {
    const { field, status, label, reason } = condition;
    if (isGroupRow(condition)) return false;
    if (field !== 'income' || status !== 'needs_check') return true;
    const key = JSON.stringify([label, reason]);
    if (incomeNotices.has(key)) return false;
    incomeNotices.add(key);
    return true;
  });
  const showVerdict = hasOnlyAndGroups(p.conditions);
  const hidesVerdict = !showVerdict && displayedConditions.some(({ status }) => status !== 'needs_check');
  const [rcls, rlabel] = RECEPTION_LABEL[p.reception_status];
  const labelsByBadge = new Map<string, { cls: string; labels: Set<string> }>();
  for (const { status, label } of displayedConditions) {
    const [cls, badge] = conditionBadge(status, showVerdict);
    const entry = labelsByBadge.get(badge) ?? { cls, labels: new Set<string>() };
    entry.labels.add(label);
    labelsByBadge.set(badge, entry);
  }
  const sourceHref = safeHttpUrl(p.source_url);
  const applyHref = safeHttpUrl(p.official_apply_url);
  // '신청하기'는 신청 주소가 확인됐고 출처 주소와 다를 때만 보여 준다.
  // 예시 응답은 실제 주소가 없으므로 버튼 모양만 보여 주고 알림을 띄운다.
  const hasApplyLink = sample
    ? !!p.official_apply_url && p.official_apply_url !== p.source_url
    : !!applyHref && applyHref !== sourceHref;
  const canApply = hasApplyLink && p.reception_status !== 'closed' && p.reception_status !== 'before';
  return (
    <details className="pcard" name={group}>
      <summary className="ptop">
        <b>{p.policy_name}</b>
        {p.reception_status !== 'unknown' && <span className={'badge ' + rcls}>{rlabel}{sample ? ' (예시)' : ''}</span>}
      </summary>
      <div className="policy-meta">
        {p.agency && <small>{p.agency}</small>}
        {p.reception_status === 'unknown' && <span className={'badge ' + rcls}>{rlabel}{sample ? ' (예시)' : ''}</span>}
      </div>
      <StageChips stages={stages} />
      <ul className="rows">
        <li>
          <span className="k">신청 기간</span>
          <span className="why">{period || '공식 원문에서 확인해 주세요.'}</span>
        </li>
      </ul>
      {unjudged ? (
        // 비교할 조건이 없다는 사유만 있으면 '추가 확인 필요' 대신 미판정 상태와 기준을 볼 곳만 안내한다.
        // 원문을 인용했어도 답변이 신청 방법만 다룰 수 있어, 답변에 기준이 있다고 가정하지 않는다.
        <div className="policy-condition-summary">
          <div><span className="badge b-muted">개인 자격 미판정</span></div>
          <p className="policy-condition-note">신청 자격은 공식 원문에서 확인할 수 있어요.</p>
        </div>
      ) : displayedConditions.length > 0 && (
        <>
          <div className="policy-condition-summary">
            {Array.from(labelsByBadge, ([badge, { cls, labels }]) => (
              <div key={badge}>
                <span className={`badge ${cls}`}>{badge}</span>
                <span>{Array.from(labels).join(' · ')}</span>
              </div>
            ))}
            {matchesExclusion(p.conditions) && <p className="exclusion-warn">{EXCLUSION_NOTICE}</p>}
            {hidesVerdict && (
              <p className="policy-condition-note">
                조건이 어떻게 결합되는지 확인되지 않았어요. 각 조건의 비교 결과만으로 신청 자격을 판단할 수 없어요.
              </p>
            )}
          </div>
          <details className="policy-reasons">
            <summary>판정 사유 자세히 보기</summary>
            <ul className="rows">
              {displayedConditions.map((c, i) => {
            const [cls, label] = conditionBadge(c.status, showVerdict);
            return (
              // 같은 field의 조건이 여러 개 올 수 있어(계약 §4) 순번을 함께 key로 쓴다
              <li key={`${c.field}-${i}`} style={{ animationDelay: `${0.25 + i * 0.14}s` }}>
                <span className="k">{c.label}</span><span className="why">{conditionReasonForDisplay(c.reason)}</span><span className={'badge ' + cls}>{label}</span>
              </li>
            );
              })}
            </ul>
          </details>
        </>
      )}
      <div className="pfoot">
        <span className="note">조건 하나하나의 충족이 최종 자격을 뜻하지는 않아요</span>
        {/* 예시 정책은 실제 데이터에 없어 관계 지도로 연결하지 않는다 */}
        {!sample && <a className="btn btn-o" href={mapHref(p.policy_id)}>관계 지도</a>}
        {sourceHref && <a className="btn btn-o" href={sourceHref} target="_blank" rel="noopener noreferrer">원문 보기 ↗</a>}
        {canApply && (sample
          ? <button type="button" className="btn btn-p" onClick={onDemoApply}>신청하기</button>
          : <a className="btn btn-p" href={applyHref!} target="_blank" rel="noopener noreferrer">신청하기 ↗</a>)}
      </div>
    </details>
  );
}

export function NoticeBox({ kind, icon, title, text }: { kind: 'info' | 'warn' | 'bad'; icon: IconName; title: string; text: string }) {
  return (
    <div className={'notice ' + kind}>
      <Icon name={icon} />
      <div><b>{title}</b><AnswerText text={text} /></div>
    </div>
  );
}
