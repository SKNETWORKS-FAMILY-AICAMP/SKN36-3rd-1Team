/**
 * 예시 응답 (sample_only).
 * 백엔드 연동 전 화면 흐름을 시연하기 위한 가상 응답이다. '예시 정책 A'와 조건 판정 기준(만 19~34세, 전국)은
 * 실제 정책이 아니다. 실제 사이트 링크는 각 기관 메인 페이지만 쓴다.
 * VITE_ASK_API_URL을 설정하면 이 파일은 쓰이지 않는다.
 * 응답 모양은 실제 백엔드(ask_contract v1.0)와 같게 만든다: ask_info는 age·region·income만,
 * 근거 없음은 notice 없이 고정 문장만 보낸다.
 */
import type { AskRequest, AskResponse, ConditionResult, FollowUp, PolicyCheck, UserProfile } from '../api/types';
import { CATEGORIES } from '../config/categories';
import { describeProfile, hasProfileInfo, parseProfile } from '../lib/parseProfile';

const SAMPLE_CHECKED_AT = '2026-11-06'; // 시연용 예시 날짜

const SRC = {
  youth: { title: '온통청년', url: 'https://www.youthcenter.go.kr/' },
  lh: { title: 'LH청약플러스', url: 'https://apply.lh.or.kr/' },
  bokjiro: { title: '복지로', url: 'https://www.bokjiro.go.kr/' },
  gov24: { title: '정부24', url: 'https://www.gov.kr/' },
};

interface Example {
  key: string;
  question: string;
  answer: string;
  citations: { title: string; url: string }[];
  followUps: FollowUp[];
  withPolicy?: boolean;
  noApplyUrl?: boolean;
}

const q = (key: string) => CATEGORIES.find((c) => c.key === key)!.question;
const askInfo = (field: string, text: string): FollowUp => ({ kind: 'ask_info', field, text });
const askQuestion = (text: string): FollowUp => ({ kind: 'question', text });

/** 분야 카드 질문(config/categories)에 대한 예시 답변 */
export const EXAMPLES: Example[] = [
  { key: 'work', question: q('work'), withPolicy: true,
    answer: '청년 정책은 온통청년에서 지역·분야별로 모아 볼 수 있어요[1]. 실제 서비스에서는 검색된 정책 원문을 근거로 나이·지역 조건을 하나씩 확인해 드려요. 아래 정책 카드는 화면 구성을 보여 주기 위한 가상 예시예요.',
    citations: [SRC.youth], followUps: [askInfo('income', '연 소득도 알려줄게요'), askQuestion('신청 서류는 뭐가 필요해요?')] },
  { key: 'housing', question: q('housing'), noApplyUrl: true,
    answer: '임대주택 모집공고와 첨부 서류 목록은 LH청약플러스에서 확인할 수 있어요[1]. 공고마다 제출 서류가 달라서, 실제 서비스에서는 해당 공고 원문의 서류 항목을 그대로 인용해 드려요.',
    citations: [SRC.lh], followUps: [askQuestion('혼인 기간 조건도 알려줘'), askQuestion('주거 대출 정책도 알려줘')] },
  { key: 'edu', question: q('edu'),
    answer: '청년 대상 교육·훈련 정책은 온통청년에서 분야별로 찾아볼 수 있어요[1]. 실제 서비스에서는 훈련 과정별 원문 공고를 근거로 대상과 신청 기간을 정리해 드려요.',
    citations: [SRC.youth], followUps: [askQuestion('재학생·직장인 조건도 알려줘')] },
  { key: 'money', question: q('money'),
    answer: '복지·금융 지원은 복지로[1]와 정부24[2]에서 서비스별로 확인할 수 있어요. 소득·재산 기준이 붙는 경우가 많아, 실제 서비스에서는 원문 기준을 인용하고 자동 판정이 어려운 항목은 ‘추가 확인 필요’로 안내해요.',
    citations: [SRC.bokjiro, SRC.gov24], followUps: [askQuestion('가구원 수 기준도 알려줘')] },
  { key: 'welfare', question: q('welfare'),
    answer: '출산·육아 관련 복지 서비스는 복지로[1]와 정부24[2]에서 찾아볼 수 있어요. 지역별로 지원이 달라서 거주 지역을 알려 주시면 더 좁혀 볼 수 있어요.',
    citations: [SRC.bokjiro, SRC.gov24], followUps: [askQuestion('자녀 나이 조건도 알려줘')] },
];

/** 가상 정책 A의 조건 판정 (예시 기준) */
function samplePolicy(p: UserProfile): PolicyCheck {
  const conditions: ConditionResult[] = [
    p.age != null
      ? { field: 'age', label: '나이', reason: `입력 ${p.age}세 · 예시 기준 만 19~34세`, status: p.age >= 19 && p.age <= 34 ? 'eligible' : 'ineligible' }
      : { field: 'age', label: '나이', reason: '나이를 알려 주시면 비교할게요', status: 'needs_check' },
    p.region
      ? { field: 'region', label: '지역', reason: `입력 ${p.region} · 예시 기준 전국`, status: 'eligible' }
      : { field: 'region', label: '지역', reason: '지역을 알려 주시면 비교할게요', status: 'needs_check' },
    // PRD: 복잡한 소득 조건은 자동 판정하지 않고 '추가 확인 필요'
    { field: 'income', label: '소득', status: 'needs_check',
      reason: p.income != null ? `입력 연 ${p.income.toLocaleString('ko-KR')}만 원 · 원문 기준이 복잡해 자동으로 판정하지 않아요` : '원문 기준이 복잡해 자동으로 판정하지 않아요' },
  ];
  return {
    policy_id: 'POL_SAMPLE_A', policy_name: '예시 정책 A · 청년 구직 지원', agency: '시연용 가상 데이터 · 실제 정책 아님',
    reception_status: 'open', conditions, source_url: SRC.youth.url,
    official_apply_url: '#sample-apply', // 시연용. 화면에서 실제로 이동하지 않는다
  };
}

function build(ex: Example, profile: UserProfile, answer = ex.answer): AskResponse {
  // 이미 알고 있는 정보는 다시 묻지 않는다
  const followUps = ex.followUps.filter((f) => !(f.field === 'income' && profile.income != null));
  if (!profile.region) followUps.unshift(askInfo('region', '지역도 알려줄게요'));
  return {
    status: 'answered', answer, citations: ex.citations,
    policy_checks: ex.withPolicy ? [samplePolicy(profile)] : [],
    follow_up_questions: followUps, checked_at: SAMPLE_CHECKED_AT, is_sample: true,
    notice: ex.noApplyUrl ? { kind: 'info', title: '신청 주소는 공고마다 달라요', text: '확인된 신청 주소가 없어서 출처만 보여 드려요.' } : null,
  };
}

const findExample = (q: string) => EXAMPLES.find((e) => e.question === q.trim());
const failedOnce = new Set<string>();
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function mockAsk(req: AskRequest): Promise<AskResponse> {
  await wait(1100);

  // 시연: 질문에 '오류'가 들어 있으면 처음 한 번은 실패한다 → '다시 시도'로 성공
  if (req.question.includes('오류') && !failedOnce.has(req.question)) {
    failedOnce.add(req.question);
    throw new Error('예시 오류: 답변 서버에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.');
  }

  const direct = findExample(req.question);
  if (direct) return build(direct, req.profile);

  // 앞 대화에서 마지막으로 답한 예시를 이어서 쓴다
  const context = [...req.history].reverse().map((m) => (m.role === 'user' ? findExample(m.text) : undefined)).find(Boolean);
  if (context) {
    const said = parseProfile(req.question, req.awaiting_field);
    if (hasProfileInfo(said)) {
      const intro = `알려 주신 조건(${describeProfile(said)})으로 다시 확인했어요.` + (context.withPolicy
        ? ' 나이·지역은 예시 기준과 비교했고, 소득은 원문 기준이 복잡해서 ‘추가 확인 필요’로 남겨 뒀어요.'
        : ' 이 질문에는 조건을 비교할 정책 카드가 없어서 확인할 곳을 다시 안내해 드려요.');
      return build(context, req.profile, intro);
    }
    if (/서류/.test(req.question)) {
      return build(context, req.profile, '제출 서류는 정책마다, 같은 정책이라도 공고마다 달라요[1]. 실제 서비스에서는 해당 공고 원문의 ‘제출 서류’ 항목을 그대로 인용하고, 원문에 없으면 ‘확인 필요’라고 알려 드려요.');
    }
    // 예시 답변의 추가 질문 버튼(혼인 기간 조건 등): 예시 데이터에는 그 조건 원문이 없으므로 확정 답 없이 안내만 한다
    if (context.followUps.some((f) => f.kind === 'question' && f.text === req.question.trim())) {
      return { status: 'info', citations: [], policy_checks: [], follow_up_questions: [], checked_at: null, is_sample: true, notice: null,
        answer: '예시 응답에는 이 조건을 확인할 공고 원문이 없어요. 실제 서비스에서는 해당 공고 원문의 조건 항목을 찾아 인용해 드려요.' };
    }
  }

  // 실제 백엔드처럼 notice 없이 고정 문장만 보낸다. 화면은 이 상태에서 자체 안내 문구를 보여 준다
  return { status: 'no_evidence', answer: '인용할 수 있는 공식 원문을 찾지 못했습니다.', citations: [], policy_checks: [],
    follow_up_questions: [], checked_at: null, is_sample: true, notice: null };
}
