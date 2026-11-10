import { useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent, type MouseEvent as ReactMouseEvent, type ReactNode, type RefObject } from 'react';
import { REGIONS } from '../lib/parseProfile';
import { Icon, type IconName } from './Icon';
import { MascotPeek } from './Logo';
import type { Category } from '../config/categories';

interface Props {
  age: string;
  ageError: string | null;
  region: string;
  income: number | null;
  onIncome: (value: number | null) => void;
  onAge: (v: string) => void;
  onRegion: (v: string) => void;
  categories: Category[];
  onAsk: (text: string, origin: HTMLElement | null) => boolean;
  askBoxRef: RefObject<HTMLDivElement>;
  inputRef: RefObject<HTMLInputElement>;
  reduceMotion: boolean;
}

const TITLE: [string, boolean, boolean][] = [['나에게', false, false], ['필요한', false, false], ['정부 지원 정책', true, false], ['을', false, true], ['물어보세요!', false, false]];

export function Hero({ age, ageError, region, income, onIncome, onAge, onRegion, categories, onAsk, askBoxRef, inputRef, reduceMotion }: Props) {
  const heroRef = useRef<HTMLElement>(null);
  useParallax(heroRef, reduceMotion);
  const examples = useMemo(() => categories.map((c) => '예: ' + c.question), [categories]);

  let delay = 0;
  return (
    <section className="hero" id="top" ref={heroRef}>
      <span className="blob a" /><span className="blob b" /><span className="blob c" />
      <Deco name="sparkles" depth={18} style={{ left: '6%', top: 64, width: 54, height: 54 }} />
      <Deco name="cloud" depth={26} style={{ left: '8%', top: 200, width: 72, height: 72 }} delay={-1.5} />
      <Deco name="bulb" depth={22} style={{ right: '7%', top: 56, width: 66, height: 66 }} delay={-3} />
      <Deco name="star" depth={14} style={{ right: '9%', top: 220, width: 46, height: 46 }} delay={-2} />

      <div className="wrap">
        <h1>
          {TITLE.map(([word, strong, glue], i) => {
            delay += 0.08;
            const Tag = strong ? 'b' : 'span';
            return (
              <span key={word}>
                {i > 0 && !glue ? ' ' : ''}
                <Tag className="w" style={{ animationDelay: strong ? `${delay}s, 0s` : `${delay}s` }}>{word}</Tag>
              </span>
            );
          })}
        </h1>
        <p className="sub">공식 원문을 근거로 조건과 신청 방법을 정리해 드려요</p>

        <div className="ask" ref={askBoxRef}>
          {/* SSOK 캐릭터가 검색창 위 테두리에 두 손을 걸치고 빼꼼 내다본다 (장식) */}
          <MascotPeek />
          <AskForm examples={examples} onAsk={onAsk} inputRef={inputRef} reduceMotion={reduceMotion} />
        </div>

        <p className="cond-hint"><b>내 조건</b>을 넣으면 정책 조건과 바로 비교해 드려요 <span>(선택)</span></p>
        <div className="conds-in" aria-label="내 조건">
          <label className={'cin' + (ageError ? ' invalid' : age ? ' filled' : '')}>
            <Icon name="cake" className="ic" /><span className="lbl">나이</span>
            <input id="age" type="text" inputMode="numeric" maxLength={3} placeholder="입력" value={age} onChange={(e) => onAge(e.target.value)}
              aria-invalid={!!ageError} aria-describedby={ageError ? 'age-error' : undefined} />
            <small>세</small>
          </label>
          <RegionPicker region={region} onRegion={onRegion} />
          <label className={'cin' + (income != null ? ' filled' : '')}>
            <span className="lbl">연 소득</span>
            <input type="number" min={0} step="any" placeholder="입력" value={income ?? ''}
              onChange={(event) => {
                const value = event.currentTarget.valueAsNumber;
                if (!Number.isFinite(value)) onIncome(null);
                else if (value >= 0) onIncome(value);
              }} />
            <small>만 원</small>
          </label>
        </div>

        {ageError && <p className="cond-error" id="age-error" role="alert">{ageError}</p>}

        <div className="cats" id="cats">
          {categories.map((c, i) => (
            <TiltCard key={c.key} className={`cat k${i + 1}`} reduceMotion={reduceMotion} onClick={(el) => onAsk(c.question, el.querySelector('.em'))}>
              <small>{c.sub}</small><b>{c.title}</b><span className="go-arr">질문하기 →</span><Icon name={c.icon} className="em" hideFace />
            </TiltCard>
          ))}
        </div>
      </div>
    </section>
  );
}

function RegionPicker({ region, onRegion }: Pick<Props, 'region' | 'onRegion'>) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);

  return (
    <div className="region-picker" onBlur={(e) => {
      if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false);
    }} onKeyDown={(e) => {
      if (open && e.key === 'Escape') {
        setOpen(false);
        trigger.current?.focus();
      }
    }}>
      <button ref={trigger} id="region" type="button" className={'cin region-trigger' + (region ? ' filled' : '')}
        aria-expanded={open} aria-controls="region-options" onClick={() => setOpen(!open)}>
        <Icon name="pin" className="ic" /><span className="lbl">지역</span>
        <span className="region-value">{region || '선택'}</span><span className="region-arrow" aria-hidden="true" />
      </button>
      <div id="region-options" className="region-options" hidden={!open} role="group" aria-label="지역 선택">
        {['', ...REGIONS].map((value) => (
          <button key={value} type="button" aria-pressed={region === value} onClick={() => {
            onRegion(value);
            setOpen(false);
            trigger.current?.focus();
          }}>{value || '선택 안 함'}</button>
        ))}
      </div>
    </div>
  );
}

/**
 * 큰 질문 입력창. 안내 문구가 한 글자씩 바뀌어도 이 입력창만 다시 그리도록
 * 입력값과 타자 효과 상태를 첫 화면 전체(Hero)와 분리해 둔다.
 */
function AskForm({ examples, onAsk, inputRef, reduceMotion }: {
  examples: string[]; onAsk: Props['onAsk']; inputRef: RefObject<HTMLInputElement>; reduceMotion: boolean;
}) {
  const [text, setText] = useState('');
  const placeholder = useTypewriter(examples, reduceMotion || text.length > 0);
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!text.trim()) return;
    if (onAsk(text, e.currentTarget.querySelector('.go'))) setText('');
  };
  return (
    <form onSubmit={submit}>
      <input ref={inputRef} value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} aria-label="정책 질문" autoComplete="off" />
      <button className="go" type="submit" aria-label="질문하기" disabled={!text.trim()}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round"><circle cx="11" cy="11" r="6.5" /><path d="M20 20l-4.2-4.2" /></svg>
      </button>
    </form>
  );
}

function Deco({ name, depth, style, delay = 0 }: { name: IconName; depth: number; style: CSSProperties; delay?: number }) {
  return (
    <span className="deco" data-depth={depth} style={style} aria-hidden="true">
      <Icon name={name} style={{ animationDelay: `${delay}s` }} hideFace />
    </span>
  );
}

/** 마우스 방향으로 살짝 기우는 카드 */
function TiltCard({ className, onClick, reduceMotion, children }: { className: string; onClick: (el: HTMLButtonElement) => void; reduceMotion: boolean; children: ReactNode }) {
  const ref = useRef<HTMLButtonElement>(null);
  const [entered, setEntered] = useState(false);
  const move = (e: ReactMouseEvent) => {
    if (reduceMotion || !entered || !ref.current) return;
    const r = ref.current.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    ref.current.style.transform = `rotateX(${-y * 10}deg) rotateY(${x * 12}deg) translateY(-4px)`;
  };
  const leave = () => { if (ref.current) ref.current.style.transform = 'none'; };
  return (
    <button ref={ref} type="button" className={className + (entered ? ' entered' : '')} onAnimationEnd={() => setEntered(true)}
      onMouseMove={move} onMouseLeave={leave} onClick={() => ref.current && onClick(ref.current)}>
      {children}
    </button>
  );
}

/** 입력창 안내 문구를 예시 질문으로 한 글자씩 바꿔 보여 준다. */
function useTypewriter(lines: string[], paused: boolean) {
  const [value, setValue] = useState(lines[0] ?? '');
  const linesKey = lines.join('|'); // 문구 목록이 실제로 바뀔 때만 다시 시작한다
  useEffect(() => {
    if (paused) return;
    let li = 0, ci = 0, del = false, timer = 0;
    const tick = () => {
      const s = lines[li];
      setValue(s.slice(0, ci));
      if (!del && ci < s.length) ci++;
      else if (!del) { del = true; timer = window.setTimeout(tick, 1800); return; }
      else if (ci > 0) ci--;
      else { del = false; li = (li + 1) % lines.length; }
      timer = window.setTimeout(tick, del ? 22 : 55);
    };
    tick();
    return () => clearTimeout(timer);
  }, [paused, linesKey]);
  return value;
}

/** 마우스를 움직이면 장식 아이콘이 반대 방향으로 살짝 따라온다. */
function useParallax(ref: RefObject<HTMLElement>, off: boolean) {
  useEffect(() => {
    const hero = ref.current;
    if (!hero || off) return;
    const decos = [...hero.querySelectorAll<HTMLElement>('.deco')];
    const onMove = (e: MouseEvent) => {
      const r = hero.getBoundingClientRect();
      const x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      decos.forEach((d) => { const k = Number(d.dataset.depth); d.style.transform = `translate(${-x * k}px,${-y * k}px)`; });
    };
    hero.addEventListener('mousemove', onMove);
    return () => hero.removeEventListener('mousemove', onMove);
  }, [ref, off]);
}
