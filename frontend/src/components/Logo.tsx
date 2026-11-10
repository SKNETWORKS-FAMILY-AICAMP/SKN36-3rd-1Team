/**
 * SSOK 로고: 둥근 세이지 글자와 별이 들어간 꿀색 돋보기.
 * 글꼴을 내려받지 못해도 모양이 같도록 글자도 선(path)으로 그린다. 색은 styles.css의 --logo-* 토큰.
 * 공고문 캐릭터는 답변 카드와 첫 화면 검색창에서 쓴다.
 */

/** S 한 글자의 가운데 선 (굵은 둥근 선으로 그린다). 높이 25(17.5~42.5) */
const S_PATH = 'M21 20.5C18 18 15 17.5 11.5 17.5C5.5 17.5 2 20.5 2 24C2 28 6 29 11.5 30C17 31 21 32.5 21 36.3C21 40 17.5 42.5 11.5 42.5C7 42.5 3.5 41.5 1 39';
/** 글자 위치 (viewBox 가로) */
const S_X = [62, 94];
const O = { cx: 141, cy: 30, r: 13 };
const STAR = 'M0 -6.5Q1 -1 6.5 0Q1 1 0 6.5Q-1 1 -6.5 0Q-1 -1 0 -6.5Z';
/** 캐릭터 선 모양 (모든 조각 공통) */
const LINE = { strokeLinecap: 'round', strokeLinejoin: 'round' } as const;

/** 공고문 캐릭터 (접힌 모서리·눈·입·볼) */
function Paper() {
  return (
    <g className="logo-peek">
      <path d="M9 36V10Q9 4 15 4H33L43 14V36Z" fill="var(--logo-paper)" stroke="var(--logo-line)" strokeWidth="2.4" />
      <path d="M33 4V11Q33 14 36 14H43" fill="var(--logo-fold)" stroke="var(--logo-line)" strokeWidth="2.4" />
      <circle cx="16.5" cy="24" r="2.6" fill="var(--logo-cheek)" />
      <circle cx="35.5" cy="24" r="2.6" fill="var(--logo-cheek)" />
      <path d="M20.5 17.5V20.5M31.5 17.5V20.5M23 23Q26 26.2 29 23" fill="none" stroke="var(--logo-line)" strokeWidth="2.4" />
    </g>
  );
}

/** 테두리에 걸친 두 손 */
function Hands() {
  return (
    <>
      <ellipse cx="14.5" cy="33.5" rx="5.4" ry="3.6" fill="var(--logo-paper)" stroke="var(--logo-line)" strokeWidth="2.2" />
      <ellipse cx="37.5" cy="33.5" rx="5.4" ry="3.6" fill="var(--logo-paper)" stroke="var(--logo-line)" strokeWidth="2.2" />
    </>
  );
}

export function Logo() {
  return (
    <svg className="logo-svg" viewBox="55 7 140 46" role="img" aria-label="SSOK">
      <g fill="none" stroke="var(--logo-text)" strokeWidth="9" {...LINE}>
        {S_X.map((x) => <path key={x} d={S_PATH} transform={`translate(${x} 0)`} />)}
        <circle cx={O.cx} cy={O.cy} r={O.r} fill="var(--logo-lens)" stroke="var(--logo-o)" strokeWidth="8" />
        <path d={`M${O.cx + 10} ${O.cy + 10}l4 4`} stroke="var(--logo-o)" strokeWidth="6" />
        <path d={STAR} transform={`translate(${O.cx} ${O.cy}) scale(.72)`} fill="var(--logo-star)" stroke="none" />
        <path d="M168 17.5V42.5M185 17.5L171 30.5M174.5 28L186 42.5" />
      </g>
    </svg>
  );
}

/** 답변 카드 머리·빈 화면에 쓰는 SSOK 캐릭터 얼굴 (공고문만) */
export function MascotFace({ className }: { className?: string }) {
  return (
    <svg className={'mascot-face' + (className ? ` ${className}` : '')} viewBox="6 1 40 37" aria-hidden="true">
      <g {...LINE}><Paper /></g>
    </svg>
  );
}

/** 첫 화면 검색창 위로 빼꼼 내다보는 캐릭터 (공고문 + 테두리에 걸친 두 손). 공고문 아래 끝이 검색창 위 테두리에 닿게 둔다 */
export function MascotPeek() {
  return (
    <svg className="mascot-peek" viewBox="2 1 48 37" aria-hidden="true">
      <g {...LINE}><Paper /><Hands /></g>
    </svg>
  );
}
