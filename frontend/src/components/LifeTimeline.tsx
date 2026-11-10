import { useEffect, useMemo, useRef, useState } from 'react';
import { LIFE_STAGES } from '../config/graph';
import { policyQuestion } from '../config/request';
import { loadGraphDataset, type GraphDataset } from '../graph/loadGraph';
import {
  formatAgeRange, groupByCategory, lifeStageSummary, stageForAge, type AgeRange, type LifeStage, type StagePolicy,
} from '../graph/lifeStages';
import { lifeHref, mapHref } from '../lib/route';
import type { RecentPolicies } from '../lib/recentPolicies';
import { safeHttpUrl } from '../lib/validate';
import { Icon } from './Icon';

type LoadState = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; data: GraphDataset };

/**
 * 생애주기 타임라인: 영유아 → 노년 생애단계마다 데이터에 연결된 정책을 분야별로 보여 준다.
 * 생애단계는 데이터의 분류라 넓게 붙은 정책도 있다. 나이 조건이 확인된 정책과 분류만 있는 정책을 구분해 표시하고,
 * 어느 쪽도 자격 확정으로 표현하지 않는다.
 */
export default function LifeTimeline({ stageName, myAge, onAsk, recent }: {
  stageName: string | null;
  myAge: number | null;
  /** 상담에서 가장 최근에 나온 정책. 그 정책이 들어 있는 단계·분야를 먼저 열고 표시한다 */
  recent: RecentPolicies | null;
  /** 상담 화면으로 가서 질문을 보낸다 */
  onAsk: (question: string) => void;
}) {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });

  useEffect(() => {
    let alive = true;
    loadGraphDataset()
      .then((data) => { if (alive) setState({ kind: 'ready', data }); })
      .catch((err: unknown) => { if (alive) setState({ kind: 'error', message: err instanceof Error ? err.message : '정책 데이터를 읽지 못했어요.' }); });
    return () => { alive = false; };
  }, []);

  const summary = useMemo(() => (state.kind === 'ready' ? lifeStageSummary(state.data.index) : null), [state]);

  return (
    <section className="board life" id="board">
      <div className="wrap">
        <div className="board-h">
          <h2>생애주기 <span>타임라인</span></h2>
          <p>태어나서 노년까지, 단계마다 연결된 정책을 모아 봤어요</p>
        </div>
        {state.kind === 'loading' && <div className="map-msg" role="status">정책 데이터를 불러오고 있어요…</div>}
        {state.kind === 'error' && <div className="notice bad"><Icon name="warning" /><div><b>타임라인을 열지 못했어요</b><p>{state.message}</p></div></div>}
        {state.kind === 'ready' && summary && (
          <Timeline data={state.data} summary={summary} stageName={stageName} myAge={myAge} onAsk={onAsk} recent={recent} />
        )}
      </div>
    </section>
  );
}

function Timeline({ data, summary, stageName, myAge, onAsk, recent }: {
  data: GraphDataset; summary: ReturnType<typeof lifeStageSummary>; stageName: string | null; myAge: number | null;
  onAsk: (question: string) => void; recent: RecentPolicies | null;
}) {
  const { stages, unclassified } = summary;
  const myStage = stageForAge(stages, myAge);
  const recentIds = useMemo(() => new Set(recent?.policies.map((p) => p.id) ?? []), [recent]);
  // 상담 정책이 들어 있는 단계: 렌더마다 단계별 정책을 다시 훑지 않게 한 번만 계산한다
  const recentStageIds = useMemo(
    () => new Set(stages.filter((s) => s.policies.some((p) => recentIds.has(p.policy.id))).map((s) => s.id)),
    [stages, recentIds],
  );
  const hasRecent = (s: LifeStage) => recentStageIds.has(s.id);
  // 고른 단계 → 방금 상담에 나온 정책이 있는 단계 → 내 나이 단계 → 정책이 가장 많은 단계 순으로 연다
  const selected = stages.find((s) => s.name === stageName)
    ?? stages.find(hasRecent)
    ?? stages.find((s) => s.id === myStage)
    ?? [...stages].sort((a, b) => b.policies.length - a.policies.length)[0];
  const withAge = selected.policies.filter((p) => limitedAges(p.ages).length > 0).length;

  // 좁은 화면에서는 타임라인이 옆으로 넘어가므로, 고른 단계가 보이게 가로로만 옮긴다 (페이지는 움직이지 않게)
  const lineRef = useRef<HTMLOListElement>(null);
  useEffect(() => {
    const line = lineRef.current;
    const on = line?.querySelector<HTMLElement>('.life-stage.on');
    if (line && on && line.scrollWidth > line.clientWidth) {
      line.scrollLeft = on.offsetLeft - (line.clientWidth - on.offsetWidth) / 2;
    }
  }, [selected.id]);

  return (
    <>
      <ol className="life-line" aria-label="생애단계" ref={lineRef}>
        {stages.map((s) => {
          const icon = LIFE_STAGES.find((l) => l.id === s.id)!.icon;
          const on = s.id === selected.id;
          return (
            <li key={s.id}>
              <a href={lifeHref(s.name)} className={'life-stage' + (on ? ' on' : '')} aria-current={on ? 'step' : undefined}>
                {s.id === myStage && <span className="life-me">지금 나 · {myAge}세</span>}
                {hasRecent(s) && <span className="life-chat" title="방금 상담에서 나온 정책이 있어요">상담</span>}
                <Icon name={icon} className="ic" />
                <b>{s.name}</b>
                <small>{s.range ? stageRangeText(s.range) : '나이 범위 미상'}</small>
                <span className="life-count">{s.policies.length}개</span>
              </a>
            </li>
          );
        })}
      </ol>

      {/* 단계를 바꾸면 펼친 분야를 닫는다 (key로 새로 그림) */}
      <StagePanel key={selected.id} stage={selected} withAge={withAge} onAsk={onAsk} recentIds={recentIds} />

      <p className="life-foot">
        데이터 {data.version}{data.status ? ` · ${data.status}` : ''} · 정책 {data.index.policies.length.toLocaleString('ko-KR')}개 중 생애단계가 연결된 정책만 보여 드려요 (미분류 {unclassified}개)
      </p>
    </>
  );
}

/**
 * 고른 생애단계의 정책: 처음에는 분야 칩만 보여 주고, 누른 분야 하나만 펼친다 (한 번에 정보가 쏟아지지 않게).
 */
function StagePanel({ stage, withAge, onAsk, recentIds }: {
  stage: LifeStage; withAge: number; onAsk: (question: string) => void; recentIds: Set<string>;
}) {
  const groups = useMemo(() => groupByCategory(stage.policies), [stage]);
  const isRecent = (g: { items: StagePolicy[] }) => g.items.some((i) => recentIds.has(i.policy.id));
  // 방금 상담에 나온 정책이 있는 분야는 처음부터 펼쳐 둔다
  const [open, setOpen] = useState<string | null>(() => groups.find(isRecent)?.category ?? null);
  // 펼친 정책 하나 (분야를 바꾸면 닫는다)
  const [openPolicy, setOpenPolicy] = useState<string | null>(null);
  const opened = groups.find((g) => g.category === open);

  return (
    <div className="life-panel">
      <div className="life-head">
        <b>{stage.name}{stage.range ? ` (${formatAgeRange(stage.range)})` : ''}</b>
        <span className="map-count">정책 {stage.policies.length}개 · 나이 기준 확인 {withAge}개</span>
      </div>
      <p className="life-caution">ⓘ 생애단계는 데이터 분류예요. 나이 배지가 있는 정책만 원문 나이 기준이 확인됐어요.</p>

      {groups.length === 0
        ? <p className="map-dim">이 단계에 연결된 정책이 없어요.</p>
        : (
          <div className="life-cats" role="group" aria-label="분야">
            {groups.map((g) => {
              const on = g.category === open;
              return (
                <button key={g.category} type="button" className={'life-cat' + (on ? ' on' : '')} aria-expanded={on}
                  onClick={() => { setOpen(on ? null : g.category); setOpenPolicy(null); }}>
                  {isRecent(g) && <i className="life-dot" aria-label="상담에서 나온 정책 있음" />}{g.category}<span>{g.items.length}</span>
                </button>
              );
            })}
          </div>
        )}

      {opened
        ? <ul className="life-list" aria-label={`${opened.category} 정책`}>{opened.items.map((item) => (
            <PolicyRow key={item.policy.id} item={item} fromChat={recentIds.has(item.policy.id)} open={openPolicy === item.policy.id}
              onToggle={() => setOpenPolicy(openPolicy === item.policy.id ? null : item.policy.id)} onAsk={onAsk} />
          ))}</ul>
        : groups.length > 0 && <p className="life-pick">분야를 누르면 정책 목록이 펼쳐져요</p>}
    </div>
  );
}

/** 타임라인의 짧은 나이 표기: 0~5세, 65세 이상 */
const stageRangeText = (r: AgeRange) => (r.max == null ? `${r.min}세 이상` : `${r.min}~${r.max}세`);

/** 실제로 제한이 있는 나이 조건만 (데이터의 '0~120세'처럼 사실상 제한 없는 조건은 배지로 보여 주지 않는다) */
const limitedAges = (ages: AgeRange[]) => ages.filter((a) => !(a.min === 0 && a.max == null));

/**
 * 정책 한 줄. 누르면 그 자리에서 데이터에 정리된 요약(대상·내용·기간)과
 * 원문 보기 / 이 정책 물어보기 / 관계 지도 보기 버튼을 펼친다.
 */
function PolicyRow({ item, fromChat, open, onToggle, onAsk }: {
  item: StagePolicy; fromChat: boolean; open: boolean; onToggle: () => void; onAsk: (question: string) => void;
}) {
  const { policy } = item;
  const ages = [...new Set(limitedAges(item.ages).map((a) => formatAgeRange(a).replace('만 ', '')))];
  const text = (key: string) => (typeof policy.data[key] === 'string' && (policy.data[key] as string).trim() ? policy.data[key] as string : null);
  const region = text('region');
  const source = safeHttpUrl(text('source_url'));
  const rows: [string, string | null][] = [['지원 대상', text('eligibility')], ['지원 내용', text('benefit')], ['신청 기간', text('application_period')]];
  const detailId = `life-${policy.id}`;
  return (
    <li className={open ? 'open' : undefined}>
      <button type="button" className="life-row" aria-expanded={open} aria-controls={detailId} onClick={onToggle}>
        <span className="life-name"><b>{policy.label}</b>{region && <small> · {region}</small>}</span>
        {fromChat && <span className="badge b-chat">상담에서 나옴</span>}
        {ages.length > 0 && <span className="badge b-age">나이 {ages.join(', ')}</span>}
        <span className="life-caret" aria-hidden="true">⌄</span>
      </button>
      {open && (
        <div className="life-detail" id={detailId}>
          <dl>
            {rows.filter(([, v]) => v).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
          </dl>
          {rows.every(([, v]) => !v) && <p className="map-dim">데이터에 정리된 요약이 없어요. 원문에서 확인해 주세요.</p>}
          <p className="life-dim">데이터에 정리된 내용이에요. 최종 자격과 신청 가능 여부는 공식 원문에서 확인해 주세요.</p>
          <div className="life-actions">
            <button type="button" className="btn btn-p" onClick={() => onAsk(policyQuestion(policy.label))}>이 정책 물어보기</button>
            {source && <a className="btn btn-o" href={source} target="_blank" rel="noopener noreferrer">원문 보기 ↗</a>}
            <a className="btn btn-o" href={mapHref(policy.id)}>관계 지도 보기</a>
          </div>
        </div>
      )}
    </li>
  );
}
