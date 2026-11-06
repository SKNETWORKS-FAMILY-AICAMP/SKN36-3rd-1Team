/**
 * AI가 사용자에게 되물을 수 있는 정보 항목.
 * 백엔드는 추가 질문에 field 값만 보내고, 화면은 여기서 이름과 입력 예시를 찾아 되묻는다.
 * usedForCheck: 화면이 문장에서 읽어 '내 조건'에 반영하는 항목(나이·지역·소득)
 */
export interface InfoField {
  label: string;
  example: string;
  usedForCheck: boolean;
}

export const INFO_FIELDS: Record<string, InfoField> = {
  age: { label: '나이', example: '예: 26살이에요', usedForCheck: true },
  region: { label: '거주 지역', example: '예: 서울에 살아요', usedForCheck: true },
  income: { label: '가구 소득', example: '예: 소득은 연 4000만 원이에요', usedForCheck: true },
  marriage_period: { label: '혼인 기간', example: '예: 결혼한 지 2년 됐어요', usedForCheck: false },
  child_age: { label: '자녀 나이', example: '예: 아이가 3살이에요', usedForCheck: false },
  school_work: { label: '학교·직장 상황', example: '예: 대학 졸업 예정이에요', usedForCheck: false },
  household_size: { label: '가구원 수', example: '예: 3인 가구예요', usedForCheck: false },
};

/** 모르는 field가 와도 되물을 수 있게 기본값을 준다. */
export function infoField(field: string | undefined): InfoField {
  return (field && INFO_FIELDS[field]) || { label: '추가 정보', example: '예: 자유롭게 적어 주세요', usedForCheck: false };
}
