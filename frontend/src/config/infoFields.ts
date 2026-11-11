/**
 * AI가 사용자에게 되물을 수 있는 정보 항목.
 * 백엔드는 추가 질문에 field 값만 보내고, 화면은 여기서 이름과 입력 예시를 찾아 되묻는다.
 * 계약(ask_contract §5): ask_info의 field는 age, region, income 세 가지뿐이다.
 * 혼인 기간·자녀 나이처럼 판정에 쓰지 않는 정보는 버튼이 아니라 답변 본문에서 안내한다(ask_contract §5).
 */
export interface InfoField {
  label: string;
  example: string;
}

export const INFO_FIELDS: Record<string, InfoField> = {
  age: { label: '나이', example: '예: 26살이에요' },
  region: { label: '거주 지역', example: '예: 서울에 살아요' },
  income: { label: '연 소득', example: '예: 소득은 연 4000만 원이에요' },
};

/** 모르는 field가 와도 되물을 수 있게 기본값을 준다. */
export function infoField(field: string | null | undefined): InfoField {
  return (field && INFO_FIELDS[field]) || { label: '추가 정보', example: '예: 자유롭게 적어 주세요' };
}
