import type { IconName } from '../components/Icon';

/**
 * 첫 화면 분야 카드. 누르면 question을 그대로 질문으로 보낸다.
 * 백엔드 연동 여부와 상관없이 쓰는 화면 설정이라 예시 응답(mock)과 분리해 둔다.
 */
export interface Category {
  key: string;
  title: string;
  sub: string;
  icon: IconName;
  question: string;
}

export const CATEGORIES: Category[] = [
  { key: 'work', title: '일자리', sub: '취업·구직 지원받기', icon: 'briefcase', question: '구직 중인 청년인데 받을 수 있는 취업 지원이 있나요?' },
  { key: 'housing', title: '주거', sub: '주거비 부담 덜기', icon: 'house', question: '신혼부부 임대주택은 어떤 서류가 필요해요?' },
  { key: 'edu', title: '교육·훈련', sub: '역량 키우기', icon: 'books', question: '직업 훈련이나 교육비 지원은 어디서 찾아요?' },
  { key: 'money', title: '복지·금융', sub: '생활·자산 지키기', icon: 'money', question: '생활비나 자산 형성 지원은 어디서 확인해요?' },
  { key: 'welfare', title: '출산·육아', sub: '아이와 함께', icon: 'bottle', question: '자녀 관련 복지 지원은 어디서 확인해요?' },
];
