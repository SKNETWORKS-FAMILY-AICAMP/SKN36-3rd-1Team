import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { askPolicy, usingSample } from './api/client';
import type { AskRequest, ChatTurnMessage, FollowUp, UserProfile } from './api/types';
import { CATEGORIES } from './config/categories';
import { infoField } from './config/infoFields';
import { describeProfile, parseProfile } from './lib/parseProfile';
import { checkAge } from './lib/validate';
import { Hero } from './components/Hero';
import { TurnView, type Turn } from './components/TurnView';
import { BottomBar, JumpButtons, Toast, burst } from './components/Floating';
import { Icon, type IconName } from './components/Icon';

const josa = (w: string) => { const c = w.charCodeAt(w.length - 1) - 0xac00; return c >= 0 && c <= 11171 && c % 28 ? '을' : '를'; };

const THEME_KEY = 'lifepolicy-theme';
function readTheme(): 'light' | 'dark' {
  try { return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'; } catch { return 'light'; }
}

export default function App() {
  const reduceMotion = useMemo(() => matchMedia('(prefers-reduced-motion: reduce)').matches, []);
  const [theme, setTheme] = useState<'light' | 'dark'>(readTheme);
  const [age, setAge] = useState('');
  const [region, setRegion] = useState('');
  const [income, setIncome] = useState<number | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [awaiting, setAwaiting] = useState<string | null>(null);
  const [toast, setToast] = useState<{ text: string; icon: IconName } | null>(null);
  const [miniOn, setMiniOn] = useState(false);
  const [miniPlaceholder, setMiniPlaceholder] = useState('이어서 물어보세요');

  const askBoxRef = useRef<HTMLDivElement>(null);
  const heroInputRef = useRef<HTMLInputElement>(null);
  const miniInputRef = useRef<HTMLInputElement>(null);
  const lastTurnRef = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);
  const turnsRef = useRef<Turn[]>([]);

  // 잘못된 나이(음수, 120 초과, 소수 등)는 조건으로 보내지 않고 입력칸에 안내한다
  const ageCheck = checkAge(age);
  const ageError = ageCheck.ok ? null : ageCheck.message;
  const profile: UserProfile = useMemo(
    () => ({ age: ageCheck.ok ? ageCheck.value : null, region, income }),
    [ageCheck.ok, ageCheck.ok ? ageCheck.value : null, region, income],
  );

  useEffect(() => { turnsRef.current = turns; }, [turns]);

  // 테마: 기본은 라이트. 버튼으로 바꾼 값만 기억한다.
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* 저장 불가 환경은 무시 */ }
  }, [theme]);

  // 큰 검색창이 화면 위로 사라지면 아래 입력창을 보여 준다
  useEffect(() => {
    const check = () => { const box = askBoxRef.current; if (box) setMiniOn(box.getBoundingClientRect().bottom < 70); };
    check();
    addEventListener('scroll', check, { passive: true });
    addEventListener('resize', check);
    return () => { removeEventListener('scroll', check); removeEventListener('resize', check); };
  }, []);
  useEffect(() => { document.body.classList.toggle('has-mini', miniOn); }, [miniOn]);

  // 새 대화가 생기면 그 위치로 스크롤
  useEffect(() => {
    if (turns.length) setTimeout(() => lastTurnRef.current?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' }), 60);
  }, [turns.length, reduceMotion]);

  const showToast = useCallback((text: string, icon: IconName = 'info') => {
    setToast({ text, icon });
    window.setTimeout(() => setToast(null), 2000);
  }, []);

  const history = (list: Turn[]): ChatTurnMessage[] => list.flatMap((t): ChatTurnMessage[] => {
    if (t.kind === 'prompt') return [{ role: 'assistant', text: t.text }];
    const out: ChatTurnMessage[] = [{ role: 'user', text: t.question }];
    if (t.response?.answer) out.push({ role: 'assistant', text: t.response.answer });
    return out;
  });

  const run = useCallback(async (id: number, request: AskRequest) => {
    try {
      const response = await askPolicy(request);
      setTurns((list) => list.map((t) => (t.id === id && t.kind === 'qa' ? { ...t, state: 'done', response } : t)));
    } catch (err) {
      const message = err instanceof Error ? err.message : '알 수 없는 오류';
      setTurns((list) => list.map((t) => (t.id === id && t.kind === 'qa' ? { ...t, state: 'error', error: message } : t)));
    }
  }, []);

  const requests = useRef(new Map<number, AskRequest>());

  /** 질문 보내기: 질문 속 나이·지역·소득을 먼저 '내 조건'에 반영한다. */
  const ask = useCallback((raw: string, origin: HTMLElement | null) => {
    const question = raw.trim();
    if (!question) return;
    burst(origin, reduceMotion);

    const found = parseProfile(question);
    if (awaiting) delete found.age; // 되물은 항목(예: 자녀 나이)에 대한 '3살'은 내 나이가 아니다
    const next: UserProfile = {
      age: found.age ?? profile.age, region: found.region ?? profile.region, income: found.income ?? profile.income,
    };
    if (found.age != null) setAge(String(found.age));
    if (found.region) setRegion(found.region);
    if (found.income != null) setIncome(found.income);

    const id = nextId.current++;
    const tags: { icon: IconName; text: string }[] = [];
    if (next.age != null) tags.push({ icon: 'cake', text: `${next.age}세` });
    if (next.region) tags.push({ icon: 'pin', text: next.region });

    const request: AskRequest = { question, profile: next, history: history(turnsRef.current), awaiting_field: awaiting };
    requests.current.set(id, request);
    setTurns((list) => [...list, { id, kind: 'qa', question, tags, state: 'loading' }]);
    void run(id, request);
    setAwaiting(null);
    setMiniPlaceholder('이어서 물어보세요');
  }, [awaiting, profile, reduceMotion, run]);

  const retry = useCallback((id: number) => {
    const request = requests.current.get(id);
    if (!request) return;
    setTurns((list) => list.map((t) => (t.id === id && t.kind === 'qa' ? { ...t, state: 'loading', error: undefined } : t)));
    void run(id, request);
  }, [run]);

  /** 추가 질문 버튼: 정보 요청(ask_info)이면 AI가 먼저 되묻고, 일반 질문이면 바로 보낸다. */
  const followUp = useCallback((f: FollowUp, origin: HTMLElement) => {
    if (f.kind !== 'ask_info') { ask(f.text, origin); return; }
    const { label, example, usedForCheck } = infoField(f.field);
    // 나이·지역·소득은 화면이 문장에서 읽어 조건에 반영하므로 따로 기다리지 않는다
    setAwaiting(usedForCheck ? null : f.field ?? null);
    setTurns((list) => [...list, { id: nextId.current++, kind: 'prompt', text: `좋아요! ${label}${josa(label)} 알려 주세요. 아래 입력창에 편하게 적어 주시면 그 조건으로 다시 확인해 드릴게요. (${example})` }]);
    setMiniPlaceholder(example);
    setTimeout(() => (miniOn ? miniInputRef : heroInputRef).current?.focus({ preventScroll: true }), 300);
  }, [ask, miniOn]);

  const condText = describeProfile(profile) ? `내 조건 · ${describeProfile(profile)}` : '내 조건 없음';

  return (
    <>
      <div className="util">
        <div className="wrap"><span className="dot" /><span className="ut">학습용 시연 화면이에요{usingSample && <> · 백엔드 연동 전이라 <b>예시 응답</b>이 표시돼요</>}</span></div>
      </div>
      <header className="gnb">
        <div className="wrap">
          <div className="logo">
            <span className="mk" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg></span>
            LifePolicy <em>AI</em>
          </div>
          <div className="right">
            <button className="ghost" type="button" onClick={() => location.reload()}><span className="sp" aria-hidden="true">↺</span><span className="t">처음으로</span></button>
            <button className="ghost" type="button" aria-label="화면 테마 바꾸기" onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}><span className="sp" aria-hidden="true">◐</span><span className="t">테마</span></button>
          </div>
        </div>
      </header>

      <Hero age={age} ageError={ageError} region={region} onAge={setAge} onRegion={setRegion} categories={CATEGORIES} onAsk={ask}
        askBoxRef={askBoxRef} inputRef={heroInputRef} reduceMotion={reduceMotion} />

      <section className="board" id="board">
        <div className="wrap">
          <div className="board-h"><h2>상담 <span>결과</span></h2><p>답변마다 근거 출처를 함께 보여 드려요</p></div>
          {turns.length === 0 && (
            <div className="empty"><Icon name="mascot" /><b>아직 질문이 없어요</b>위 검색창에 물어보거나 분야 카드를 눌러 보세요.</div>
          )}
          {turns.map((t, i) => (
            <div key={t.id} ref={i === turns.length - 1 ? lastTurnRef : undefined}>
              <TurnView turn={t} onFollowUp={followUp} onRetry={retry} onDemoApply={() => showToast('시연용 버튼이에요. 실제 신청 주소로 연결되지 않아요.')} />
            </div>
          ))}
        </div>
      </section>

      <footer><div className="wrap">LifePolicy AI · SKN36 3차 프로젝트 학습용 시연. 최종 자격과 신청 가능 여부는 담당 기관의 공식 원문에서 확인해 주세요. 아이콘은 이 화면용으로 직접 그린 SVG예요.</div></footer>

      <BottomBar visible={miniOn} condText={condText} placeholder={miniPlaceholder} inputRef={miniInputRef} onAsk={ask}
        onCondClick={() => { scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' }); setTimeout(() => document.getElementById('age')?.focus(), 400); }} />
      <JumpButtons reduceMotion={reduceMotion} watch={turns} />
      <Toast message={toast} />
    </>
  );
}
