import { afterEach, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { createElement } from 'react';
import type { PolicyCheck } from '../api/types';
import { scheduleView, scheduledPolicies, kstDay, isApplicationSchedule } from './applicationSchedule';
import { PolicyScheduleList } from '../components/PolicyScheduleList';
const day='2026-11-22';
// 서버(v1.13)처럼 closing_soon·deadline_rank를 함께 보낸다(backend/eligibility/checker.py application_schedule·deadline_rank).
const rankOf={open:0,before:1,unknown:3,closed:4} as const;
const policy=(id:string,days:number,status:PolicyCheck['reception_status']='open'):PolicyCheck=>({policy_id:id,policy_name:'가상 정책 '+id,agency:'sample_only',conditions:[],source_url:'https://example.org/',official_apply_url:null,reception_status:status,deadline_rank:rankOf[status],application_schedule:{evaluated_on:day,start_date:'2026-11-01',end_date:`2026-11-${22+days}`,is_ongoing:false,days_to_start:-21,days_to_end:days,closing_soon:status==='open'&&days>=0&&days<=7}});
afterEach(()=>vi.useRealTimers());
it.each([[3,'마감 D-3'],[0,'오늘 마감'],[-1,'']])('남은 %s일 표시',(days,badge)=>expect(scheduleView(policy('A',days,days<0?'closed':'open'),day).badge).toBe(badge));
it('시작 예정과 상시는 별도 표시',()=>{const p=policy('A',7,'before');Object.assign(p.application_schedule!,{start_date:'2026-11-25',days_to_start:3});expect(scheduleView(p,day).badge).toBe('시작 D-3');p.reception_status='open';Object.assign(p.application_schedule!,{start_date:null,end_date:null,days_to_start:null,days_to_end:null,is_ongoing:true});expect(scheduleView(p,day).badge).toBe('상시 접수');expect(scheduledPolicies([p],day,'soon','deadline')).toHaveLength(0);expect(scheduledPolicies([p],day,'ongoing','deadline')).toHaveLength(1);});
it('KST 자정과 이전 응답',()=>{expect(kstDay(new Date('2026-11-21T15:00:00Z'))).toBe(day);expect(kstDay(new Date('2026-11-22T14:59:59Z'))).toBe(day);expect(scheduleView(policy('A',3),'2026-11-23').status).toBe('unknown');});
it('날짜 오류와 상태 충돌은 확인 필요',()=>{const p=policy('A',3);p.application_schedule!.end_date='2026-02-30';expect(isApplicationSchedule(p.application_schedule)).toBe(false);expect(scheduleView(p,day).status).toBe('unknown');p.application_schedule=null;expect(scheduleView(p,day).badge).toBe('');expect(scheduleView(p,day).status).toBe('open');p.application_schedule=policy('A',-1).application_schedule;expect(scheduleView(p,day).status).toBe('unknown');});
it('0~7일 필터와 정렬은 원본을 보존',()=>{const ps=[policy('C',8),policy('B',0),policy('A',3),policy('D',7),policy('E',-1,'closed')];const original=JSON.stringify(ps);expect(scheduledPolicies(ps,day,'soon','deadline').map(x=>x.policy.policy_id)).toEqual(['B','A','D']);expect(scheduledPolicies(ps,day,'all','name').map(x=>x.policy.policy_id)).toEqual(['A','B','C','D','E']);expect(JSON.stringify(ps)).toBe(original);expect(scheduledPolicies([],day,'soon','deadline')).toEqual([]);});
it('상담 범위와 정책 연결, 신청 주소 없음',()=>{vi.useFakeTimers();vi.setSystemTime(new Date('2026-11-22T03:00:00Z'));const response={status:'answered' as const,answer:'가상 응답',policy_checks:[policy('A',3)],citations:[],follow_up_questions:[],checked_at:day,is_sample:false};const html=renderToStaticMarkup(createElement(PolicyScheduleList,{response,onAsk:()=>{},onDemoApply:()=>{}}));expect(html).toContain('전체 정책 목록이 아닙니다');expect(html).toContain('마감 D-3');expect(html).toContain('가상 정책 A');expect(html).not.toContain('>신청하기');});

it('만료된 응답은 D-day와 신청 링크를 숨기지만 출처와 조건은 유지',()=>{vi.useFakeTimers();vi.setSystemTime(new Date('2026-11-23T03:00:00Z'));const p=policy('A',3);p.official_apply_url='https://example.org/apply';const response={status:'answered' as const,answer:'가상 응답',policy_checks:[p],citations:[],follow_up_questions:[],checked_at:day,is_sample:false};const html=renderToStaticMarkup(createElement(PolicyScheduleList,{response,onAsk:()=>{},onDemoApply:()=>{}}));expect(html).toContain('일정 재확인 필요');expect(html).not.toContain('마감 D-3');expect(html).not.toContain('href="https://example.org/apply"');expect(html).toContain('https://example.org/');});

it('날짜와 서버 남은 일수가 다르면 확정 표시하지 않는다',()=>{const p=policy('A',3);p.application_schedule!.days_to_end=4;expect(scheduleView(p,day).status).toBe('unknown');expect(scheduleView(p,day).badge).toBe('');});

it('기존 API의 일정 필드 생략 시 접수 상태만 보존하고 D-day는 생성하지 않는다',()=>{for(const status of ['open','before','closed','unknown'] as const){const p=policy('legacy',3,status);delete p.application_schedule;const s=scheduleView(p,day);expect(s.status).toBe(status);expect(s.badge).toBe('');expect(s.remaining).toBeNull();expect(scheduledPolicies([p],day,'soon','deadline')).toEqual([]);}});

it('마감 임박·마감순 순위는 서버 값(closing_soon·deadline_rank)을 그대로 쓴다 (#165, 계약 v1.13)',()=>{
  // 남은 일수가 7일 이내여도 서버가 closing_soon=false로 보내면 마감 임박으로 보지 않는다(화면이 다시 계산하지 않음).
  const notSoon=policy('A',3);notSoon.application_schedule!.closing_soon=false;
  expect(scheduledPolicies([notSoon],day,'soon','deadline')).toEqual([]);
  // 정렬은 서버 순위를 따르고, 순위가 없는 예전 응답은 '모집 상태 미확인'(3) 자리에 둔다.
  // 화면이 직접 계산하면 2일 남은 C가 앞이지만, 서버 순위(B=0, C=없음→3)를 따르면 B가 앞이다.
  const first=policy('B',5);
  const legacy=policy('C',2);delete legacy.deadline_rank;
  const closed=policy('D',-1,'closed');
  expect(scheduledPolicies([closed,legacy,first],day,'all','deadline').map(x=>x.policy.policy_id)).toEqual(['B','C','D']);
});
