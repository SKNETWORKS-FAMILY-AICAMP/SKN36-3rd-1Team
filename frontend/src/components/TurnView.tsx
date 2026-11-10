import { useEffect, useState, type ReactElement, type ReactNode } from 'react';
import type { AskResponse, ConditionStatus, FollowUp, PolicyCheck, ReceptionStatus } from '../api/types';
import { Icon, type IconName } from './Icon';
import { MascotFace } from './Logo';
import { safeHttpUrl } from '../lib/validate';
import { lifeHref, mapHref } from '../lib/route';
import { answerView, freshnessNote, sourceDetail } from '../lib/answerView';
import { LOADING_STAGES } from '../config/request';

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
                text={`${turn.error ?? ''}${sampleMode ? ' (시연: 질문에 “오류”를 넣으면 이 화면이 나와요)' : ''}`} />
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
function AnswerText({ text }: { text: string }) {
  return (
    <p className="answer">
      {text.split(/(\[\d+\])/).map((part, i) => {
        const m = part.match(/^\[(\d+)\]$/);
        return m ? <sup key={i}>{m[1]}</sup> : <span key={i}>{part}</span>;
      })}
    </p>
  );
}

function Answer({ r, onFollowUp, onDemoApply }: { r: AskResponse; onFollowUp: Props['onFollowUp']; onDemoApply: () => void }) {
  const view = answerView(r);
  const blocks: ReactElement[] = [];
  if (view.warning) blocks.push(<NoticeBox key="w" kind="warn" icon="search" title={view.warning.title} text={view.warning.text} />);
  if (view.answerText) blocks.push(<AnswerText key="a" text={view.answerText} />);
  if (view.guide) {
    blocks.push(<NoticeBox key="g" kind={view.guide.kind} icon={view.guide.kind === 'warn' ? 'search' : 'info'} title={view.guide.title} text={view.guide.text} />);
  }
  r.policy_checks.forEach((p) => blocks.push(
    <Block key={p.policy_id} title="조건 확인" icon="check" tag={r.is_sample ? '시연용 가상 데이터' : undefined}>
      <PolicyCard p={p} sample={r.is_sample} onDemoApply={onDemoApply} />
    </Block>,
  ));
  if (view.extraInfo) {
    blocks.push(<NoticeBox key="i" kind="info" icon="info" title={view.extraInfo.title} text={view.extraInfo.text} />);
  }
  if (r.citations.length > 0) {
    blocks.push(
      <Block key="src" title="근거 출처" icon="books">
        <div className="srcs">
          {r.citations.map((c, i) => {
            const href = safeHttpUrl(c.url);
            const inner = <><span className="n">{i + 1}</span><span><b>{c.title}</b><span>{sourceDetail(href, c.page_number)}</span></span>{href && <span className="arr">↗</span>}</>;
            // 안전하지 않은 주소는 링크로 만들지 않고 제목만 보여 준다
            return href
              ? <a key={i} className="src" href={href} target="_blank" rel="noopener noreferrer">{inner}</a>
              : <div key={i} className="src">{inner}</div>;
          })}
        </div>
        <p className="fresh">{freshnessNote(r.checked_at, r.is_sample)}</p>
      </Block>,
    );
  }
  if (r.follow_up_questions.length > 0) {
    blocks.push(
      <Block key="f" title="이것도 알려 주시면 더 정확해요" icon="bulb">
        <div className="chips">
          {r.follow_up_questions.map((f, i) => <button key={`${i}-${f.text}`} type="button" className="chip" onClick={(e) => onFollowUp(f, e.currentTarget)}>{f.text}</button>)}
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
function StageChips({ policyId }: { policyId: string }) {
  const [stages, setStages] = useState<string[]>([]);
  useEffect(() => {
    let alive = true;
    Promise.all([import('../graph/loadGraph'), import('../graph/lifeStages')])
      .then(async ([{ loadGraphDataset }, { policyLifeStages }]) => {
        const { index } = await loadGraphDataset();
        if (alive) setStages(policyLifeStages(index, policyId));
      })
      .catch(() => { /* 생애단계는 보조 정보라 못 읽어도 답변 표시는 그대로 둔다 */ });
    return () => { alive = false; };
  }, [policyId]);
  if (stages.length === 0) return null;
  return (
    <div className="pstages" aria-label="생애단계">
      {stages.map((name) => <a key={name} className="stage-chip" href={lifeHref(name)} title={`생애주기에서 '${name}' 정책 보기`}>{name}</a>)}
    </div>
  );
}

function PolicyCard({ p, sample, onDemoApply }: { p: PolicyCheck; sample: boolean; onDemoApply: () => void }) {
  const [rcls, rlabel] = RECEPTION_LABEL[p.reception_status];
  const sourceHref = safeHttpUrl(p.source_url);
  const applyHref = safeHttpUrl(p.official_apply_url);
  // '신청하기'는 신청 주소가 확인됐고 출처 주소와 다를 때만 보여 준다.
  // 예시 응답은 실제 주소가 없으므로 버튼 모양만 보여 주고 알림을 띄운다.
  const canApply = sample ? !!p.official_apply_url && p.official_apply_url !== p.source_url : !!applyHref && applyHref !== sourceHref;
  return (
    <div className="pcard">
      <div className="ptop">
        <div><b>{p.policy_name}</b><small>{p.agency}</small></div>
        <span className={'badge ' + rcls}>{rlabel}{sample ? ' (예시)' : ''}</span>
      </div>
      {/* 예시 정책은 실제 데이터에 없어 생애단계를 찾지 않는다 */}
      {!sample && <StageChips policyId={p.policy_id} />}
      {p.conditions.length > 0 && (
        <ul className="rows">
          {p.conditions.map((c, i) => {
            const [cls, label] = CONDITION_LABEL[c.status];
            return (
              // 같은 field의 조건이 여러 개 올 수 있어(계약 §4) 순번을 함께 key로 쓴다
              <li key={`${c.field}-${i}`} style={{ animationDelay: `${0.25 + i * 0.14}s` }}>
                <span className="k">{c.label}</span><span className="why">{c.reason}</span><span className={'badge ' + cls}>{label}</span>
              </li>
            );
          })}
        </ul>
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
    </div>
  );
}

export function NoticeBox({ kind, icon, title, text }: { kind: 'info' | 'warn' | 'bad'; icon: IconName; title: string; text: string }) {
  return (
    <div className={'notice ' + kind}>
      <Icon name={icon} />
      <div><b>{title}</b><p>{text}</p></div>
    </div>
  );
}
