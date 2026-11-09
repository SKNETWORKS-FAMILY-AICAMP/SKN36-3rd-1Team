/**
 * SSOK 로고: 살구색 주머니에서 빼꼼 나온 노란 공고문 캐릭터 + SSOK 글자.
 * O는 캐러멜색 돋보기(렌즈 가운데 노란 별), S·S·K는 둥근 진갈색 글자.
 * 글꼴을 내려받지 못해도 모양이 같도록 글자도 선(path)으로 그린다. 색은 styles.css의 --logo-* 토큰.
 */

/** S 한 글자의 가운데 선 (굵은 둥근 선으로 그린다). 높이 25(17.5~42.5) */
const S_PATH = 'M21 20.5C18 18 15 17.5 11.5 17.5C5.5 17.5 2 20.5 2 24C2 28 6 29 11.5 30C17 31 21 32.5 21 36.3C21 40 17.5 42.5 11.5 42.5C7 42.5 3.5 41.5 1 39';
/** 글자 위치 (viewBox 가로) */
const S_X = [62, 94];
const O = { cx: 141, cy: 30, r: 13 };
const STAR = 'M0 -6.5Q1 -1 6.5 0Q1 1 0 6.5Q-1 1 -6.5 0Q-1 -1 0 -6.5Z';

export function Logo() {
  return (
    <svg className="logo-svg" viewBox="0 0 196 60" role="img" aria-label="SSOK">
      {/* 캐릭터: 공고문(뒤) → 주머니(앞) → 주머니 테두리에 걸친 두 손 */}
      <g className="logo-mascot" strokeLinecap="round" strokeLinejoin="round">
        <g className="logo-peek">
          <path d="M9 36V10Q9 4 15 4H33L43 14V36Z" fill="var(--logo-paper)" stroke="var(--logo-line)" strokeWidth="2.4" />
          <path d="M33 4V11Q33 14 36 14H43" fill="var(--logo-fold)" stroke="var(--logo-line)" strokeWidth="2.4" />
          <circle cx="16.5" cy="24" r="2.6" fill="var(--logo-cheek)" />
          <circle cx="35.5" cy="24" r="2.6" fill="var(--logo-cheek)" />
          <path d="M20.5 17.5V20.5M31.5 17.5V20.5M23 23Q26 26.2 29 23" fill="none" stroke="var(--logo-line)" strokeWidth="2.4" />
        </g>
        <path d="M3.5 33Q26 31.6 48.5 33Q50.2 33.4 49.8 36Q48.8 46.5 45.2 51.2Q41.6 56 26 56Q10.4 56 6.8 51.2Q3.2 46.5 2.2 36Q1.8 33.4 3.5 33Z"
          fill="var(--logo-pocket)" stroke="var(--logo-line)" strokeWidth="2.4" />
        <ellipse cx="14.5" cy="33.5" rx="5.4" ry="3.6" fill="var(--logo-paper)" stroke="var(--logo-line)" strokeWidth="2.2" />
        <ellipse cx="37.5" cy="33.5" rx="5.4" ry="3.6" fill="var(--logo-paper)" stroke="var(--logo-line)" strokeWidth="2.2" />
      </g>

      {/* 글자: S S (돋보기 O) K */}
      <g fill="none" strokeLinecap="round" strokeLinejoin="round">
        {S_X.map((x) => <path key={x} d={S_PATH} transform={`translate(${x} 0)`} stroke="var(--logo-text)" strokeWidth="9" />)}
        <circle cx={O.cx} cy={O.cy} r={O.r} fill="var(--logo-lens)" stroke="var(--logo-o)" strokeWidth="8" />
        <path d={`M${O.cx + 10.5} ${O.cy + 10.5}L${O.cx + 16} ${O.cy + 16.5}`} stroke="var(--logo-o)" strokeWidth="7" />
        <path d={STAR} transform={`translate(${O.cx} ${O.cy})`} fill="var(--logo-star)" />
        <path d="M168 17.5V42.5M185 17.5L171 30.5M174.5 28L186 42.5" stroke="var(--logo-text)" strokeWidth="9" />
      </g>
    </svg>
  );
}
