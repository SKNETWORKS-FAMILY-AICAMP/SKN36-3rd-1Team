import type { IconName } from '../components/Icon';

/**
 * 첫 화면 '대표 상황으로 시작하기' 카드.
 * 출처: PRD §2 대표 시나리오 표 (가상 인물, sample_only — 실사용자 정보가 아니다).
 * PRD에 없는 값(신혼부부·육아 가구의 나이·지역 등)은 지어내지 않고 비워 둔다.
 */
export interface Persona {
  key: string;
  title: string;
  /** PRD의 '상황' 칸 */
  situation: string;
  /** PRD의 '대표 질문' 칸 */
  question: string;
  /** 누르면 '내 조건'에 넣을 값. PRD에 있는 값만 쓴다 */
  profile: { age: number | null; region: string };
  icon: IconName;
}

export const PERSONAS: Persona[] = [
  { key: 'job', title: '청년 취업준비생', situation: '29세 · 경기도 거주 · 구직 중',
    question: '교육비·구직·교통 지원 정책이 있나요?', profile: { age: 29, region: '경기' }, icon: 'personaJob' },
  { key: 'newlywed', title: '신혼 무주택 가구', situation: '혼인 2년 · 무주택',
    question: '신혼부부 임대주택은 어떤 서류가 필요하나요?', profile: { age: null, region: '' }, icon: 'personaNewlywed' },
  { key: 'parent', title: '출산·육아 가구', situation: '영유아 자녀 보유',
    question: '자녀 관련 복지 지원은 어디서 확인하나요?', profile: { age: null, region: '' }, icon: 'personaParent' },
];
