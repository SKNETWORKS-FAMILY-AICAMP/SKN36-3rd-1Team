import { useId, useState } from 'react';
import type { AskResponse } from '../api/types';
import { scheduledPolicies, type ScheduleFilter } from '../lib/applicationSchedule';
import { PolicyCard } from './TurnView';
import { useScheduleDay } from './ScheduleClock';

const filters: [ScheduleFilter, string][] = [['all', '전체'], ['open', '접수 중'], ['soon', '7일 이내 마감'], ['before', '접수 예정'], ['ongoing', '상시 접수']];
/** 전체 정책 검색이 아니라 이미 받은 최근 상담 응답만 사용한다. 필터·정렬에는 API 호출이 없다. */
export function PolicyScheduleList({ response, onAsk, onDemoApply }: { onDemoApply: () => void; response: AskResponse | null; onAsk: (question: string, origin: HTMLElement) => void }) {
  const [filter, setFilter] = useState<ScheduleFilter>('all');
  const [sort, setSort] = useState<'deadline' | 'name'>('deadline');
  const group = useId();
  const rows = scheduledPolicies(response?.policy_checks ?? [], useScheduleDay(), filter, sort);
  return <section className="schedule-section"><div className="wrap">
    <div className="board-h"><h2>최근 상담의 <span>신청 일정</span></h2><p>접수 일정과 신청 자격은 별도로 확인해 주세요.</p></div>
    <p className="schedule-scope">최근 완료된 상담에 나온 정책만 표시합니다. 전체 정책 목록이 아닙니다.{response?.is_sample && ' · 시연용 가상 응답'}</p>
    <div className="schedule-tools" role="group" aria-label="신청 일정 필터">
      {filters.map(([key, text]) => <button className="chip" type="button" key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>{text}</button>)}
      <label className="schedule-sort">정렬 <select value={sort} onChange={e => setSort(e.target.value as typeof sort)}><option value="deadline">마감 임박순</option><option value="name">이름순</option></select></label>
    </div>
    <p className="schedule-count" role="status" aria-live="polite">정책 {rows.length}개</p>
    <div className="schedule-grid">{rows.map(({ policy, schedule }) => <PolicyCard key={policy.policy_id} p={policy} period={schedule.period} stages={[]} group={group} sample={!!response?.is_sample} onDemoApply={onDemoApply} onAskPolicy={onAsk} />)}</div>
    {rows.length === 0 && <div className="schedule-empty">{response?.policy_checks.length ? '이 접수 조건에 해당하는 정책이 없어요.' : '궁금한 정책을 먼저 물어보세요. 답변에 나온 정책의 신청 일정을 여기에 모아 드려요.'}</div>}
  </div></section>;
}
