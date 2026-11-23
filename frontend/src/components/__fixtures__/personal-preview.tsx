/** sample_only: 캘린더 다운로드·저장·마감 안내 UI 전용. 제품 진입점에서 import하지 않는다. */
import { createRoot } from 'react-dom/client';
import { useEffect, useState } from 'react';
import { SavedPoliciesProvider } from '../SavedPolicies';
import { ScheduleClock } from '../ScheduleClock';
import { PolicyExplorer } from '../PolicyExplorer';
import { loadGraphDataset } from '../../graph/loadGraph';
import { catalogPolicies } from '../../lib/policyCatalog';
import { kstDay } from '../../lib/applicationSchedule';
import type { AskResponse } from '../../api/types';
import '../../styles.css';
function Preview() {
  const [response, setResponse] = useState<AskResponse | null>(null);
  useEffect(() => { loadGraphDataset().then(data => {
    const policy = catalogPolicies(data.index)[0].policy;
    const today = kstDay();
    setResponse({ status: 'answered', answer: 'sample_only', citations: [], follow_up_questions: [], checked_at: null, is_sample: false,
      policy_checks: [{ ...policy, reception_status: 'open', application_schedule: { evaluated_on: today, start_date: null, end_date: today, is_ongoing: false, days_to_start: null, days_to_end: 0 } }] });
  }); }, []);
  return <><h1>sample_only · 가상 일정 UI 검증 (실제 신청 정보 아님)</h1><button type="button" onClick={() => document.documentElement.dataset.theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'}>테마 전환</button><PolicyExplorer response={response} onAsk={() => {}} onDemoApply={() => {}} /></>;
}
createRoot(document.getElementById('root')!).render(<ScheduleClock><SavedPoliciesProvider><Preview /></SavedPoliciesProvider></ScheduleClock>);
