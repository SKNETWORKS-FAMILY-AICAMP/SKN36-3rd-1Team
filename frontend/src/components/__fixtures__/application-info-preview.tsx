/** sample_only: 계약에 있는 선택 필드를 화면에 주입. 검색·모델·DB 호출 없음. */
import { createRoot } from 'react-dom/client';
import { useState } from 'react';
import type { PolicyCheck } from '../../api/types';
import { PolicyCard } from '../TurnView';
import { ScheduleClock } from '../ScheduleClock';
import '../../styles.css';
const policies: PolicyCheck[] = [
  { policy_id: 'SAMPLE_A', policy_name: '가상 정책 A · 공식 기준 있음', agency: '가상 기관 A', reception_status: 'unknown',
    source_url: 'https://example.org/policy-a', official_apply_url: null,
    conditions: [{ field: 'income', label: '소득', status: 'needs_check', reason: '가구 소득 기준은 공고 확인이 필요해요.' }],
    official_requirements: [{ label: '소득', value: '2025년 기준 중위소득 100% 이하 (가상 예시)', document_id: 'SAMPLE_DA', chunk_id: 'SAMPLE_CA' }] },
  { policy_id: 'SAMPLE_B', policy_name: '가상 정책 B · 이전 응답', agency: '', reception_status: 'closed',
    source_url: '', official_apply_url: null,
    conditions: [{ field: 'age', label: '나이', status: 'needs_check', reason: '만 나이를 입력해 주세요.' }] },
];
function Preview() {
  const [dark, setDark] = useState(false);
  return <div className="wrap"><h1>sample_only · 가상 응답 카드 검증</h1><p>실제 정책·자격·신청 정보가 아닙니다.</p>
    <button type="button" className="chip" onClick={() => { setDark(!dark); document.documentElement.dataset.theme = dark ? 'light' : 'dark'; }}>테마 전환</button>
    <div className="schedule-grid">{policies.map(p => <PolicyCard key={p.policy_id} p={p} group="sample" period="일정 확인 필요" stages={[]} sample={true}
      sources={[{ title: '가상 기준 문서', document_id: 'SAMPLE_DA', url: 'https://example.org/requirements-a' }]} onDemoApply={() => {}} />)}</div></div>;
}
createRoot(document.getElementById('root')!).render(<ScheduleClock><Preview /></ScheduleClock>);
