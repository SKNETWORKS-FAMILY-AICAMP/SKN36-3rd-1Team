import { useEffect, useState, type FormEvent, type RefObject } from 'react';
import { iconUrl, type IconName } from './Icon';

/** 큰 검색창이 화면 밖으로 나가면 아래에서 올라오는 이어서 질문하기 입력창 */
export function BottomBar({ visible, condText, placeholder, inputRef, onAsk, onCondClick }: {
  visible: boolean; condText: string; placeholder: string; inputRef: RefObject<HTMLInputElement>;
  onAsk: (text: string, origin: HTMLElement | null) => boolean; onCondClick: () => void;
}) {
  const [text, setText] = useState('');
  const submit = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!text.trim()) return;
    if (onAsk(text, e.currentTarget.querySelector('.mini-go'))) setText('');
  };
  const tab = visible ? 0 : -1;
  return (
    <div className={'mini' + (visible ? ' on' : '')} aria-hidden={!visible}>
      <div className="wrap">
        <form onSubmit={submit}>
          <input ref={inputRef} value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} aria-label="이어서 질문하기" autoComplete="off" tabIndex={tab} />
          <button type="button" className="mini-cond" onClick={onCondClick} tabIndex={tab}>{condText}</button>
          <button type="submit" className="mini-go" disabled={!text.trim()} tabIndex={tab}>질문</button>
        </form>
      </div>
    </div>
  );
}

/** 맨 위로 / 맨 아래로. 필요할 때만 보인다. */
export function JumpButtons({ reduceMotion, watch }: { reduceMotion: boolean; watch: unknown }) {
  const [state, setState] = useState({ top: false, bottom: false });
  useEffect(() => {
    const check = () => {
      const max = document.documentElement.scrollHeight - innerHeight;
      const top = scrollY >= 300, bottom = max - scrollY >= 300;
      // 스크롤마다 새 객체를 넣으면 값이 같아도 다시 그리므로, 바뀔 때만 상태를 바꾼다
      setState((prev) => (prev.top === top && prev.bottom === bottom ? prev : { top, bottom }));
    };
    check();
    addEventListener('scroll', check, { passive: true });
    addEventListener('resize', check);
    return () => { removeEventListener('scroll', check); removeEventListener('resize', check); };
  }, [watch]);
  const behavior: ScrollBehavior = reduceMotion ? 'auto' : 'smooth';
  return (
    <div className="jump">
      <button type="button" className={state.top ? '' : 'off'} aria-label="맨 위로" title="맨 위로" onClick={() => scrollTo({ top: 0, behavior })}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M6 15l6-6 6 6" /></svg>
      </button>
      <button type="button" className={state.bottom ? '' : 'off'} aria-label="맨 아래로" title="맨 아래로" onClick={() => scrollTo({ top: document.documentElement.scrollHeight, behavior })}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M6 9l6 6 6-6" /></svg>
      </button>
    </div>
  );
}

export function Toast({ message }: { message: { text: string; icon: IconName } | null }) {
  return (
    <div className={'toast' + (message ? ' on' : '')} role="status">
      {message && <><img src={iconUrl(message.icon)} alt="" />{message.text}</>}
    </div>
  );
}

/** 보낸 자리에서 반짝이가 터지는 효과 (화면 장식이라 DOM에 잠깐 붙였다 뗀다) */
export function burst(from: HTMLElement | null, reduceMotion: boolean) {
  if (!from || reduceMotion) return;
  const r = from.getBoundingClientRect(), cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  (['sparkles', 'star', 'heart', 'sparkles', 'star', 'sparkles'] as IconName[]).forEach((k, i) => {
    const b = document.createElement('img');
    b.src = iconUrl(k); b.alt = ''; b.className = 'burst';
    const a = (Math.PI * 2 * i) / 6 + Math.random() * 0.5, dist = 50 + Math.random() * 40;
    b.style.left = `${cx - 11}px`; b.style.top = `${cy - 11}px`;
    b.style.setProperty('--dx', `${Math.cos(a) * dist}px`);
    b.style.setProperty('--dy', `${Math.sin(a) * dist}px`);
    b.style.setProperty('--r', `${Math.random() * 120 - 60}deg`);
    document.body.append(b);
    setTimeout(() => b.remove(), 950);
  });
}
