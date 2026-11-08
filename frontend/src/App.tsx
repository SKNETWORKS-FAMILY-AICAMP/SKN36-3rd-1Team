import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { askPolicy, usingSample } from './api/client';
import type { AskRequest, ChatTurnMessage, FollowUp, UserProfile } from './api/types';
import { CATEGORIES } from './config/categories';
import { infoField } from './config/infoFields';
import { PERSONAS, type Persona } from './config/personas';
import { HISTORY_MAX_ITEMS } from './config/request';
import { describeProfile, parseProfile } from './lib/parseProfile';
import { askHref, lifeHref, mapHref, useRoute } from './lib/route';
import { checkAge } from './lib/validate';
import { Hero } from './components/Hero';
import { TurnView, type Turn } from './components/TurnView';
import { PersonaStart } from './components/PersonaStart';
import { BottomBar, JumpButtons, Toast, burst } from './components/Floating';
import type { IconName } from './components/Icon';

// 관계 지도는 그 페이지를 열 때만 코드와 데이터를 내려받는다
const PolicyMap = lazy(() => import('./components/PolicyMap'));
const LifeTimeline = lazy(() => import('./components/LifeTimeline'));

const josa = (w: string) => { const c = w.charCodeAt(w.length - 1) - 0xac00; return c >= 0 && c <= 11171 && c % 28 ? '을' : '를'; };

const THEME_KEY = 'lifepolicy-theme';
function readTheme(): 'light' | 'dark' {
  try { return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'; } catch { return 'light'; }
}

export default function App() {
  const route = useRoute();
  const onAskPage = route.page === 'ask';
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

  // 큰 검색창이 화면 위로 사라지면 아래 입력창을 보여 준다 (관계 지도 화면에는 검색창이 없어 숨긴다)
  useEffect(() => {
    const check = () => { const box = askBoxRef.current; setMiniOn(box ? box.getBoundingClientRect().bottom < 70 : false); };
    check();
    addEventListener('scroll', check, { passive: true });
    addEventListener('resize', check);
    return () => { removeEventListener('scroll', check); removeEventListener('resize', check); };
  }, [onAskPage]);
  useEffect(() => { document.body.classList.toggle('has-mini', miniOn); }, [miniOn]);

  // 화면을 바꾸면 맨 위에서 시작한다. 다른 화면에서 질문을 보내고 넘어온 경우엔 그 답변 위치로 간다
  const showLatestTurn = useRef(false);
  useEffect(() => {
    if (onAskPage && showLatestTurn.current) {
      showLatestTurn.current = false;
      setTimeout(() => lastTurnRef.current?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' }), 80);
    } else {
      scrollTo({ top: 0 });
    }
  }, [route.page, onAskPage, reduceMotion]);

  // 새 대화가 생기면 그 위치로 스크롤
  useEffect(() => {
    if (turns.length) setTimeout(() => lastTurnRef.current?.scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'start' }), 60);
  }, [turns.length, reduceMotion]);

  // 알림을 연달아 띄워도 앞 알림의 타이머가 새 알림을 일찍 끄지 않도록 타이머를 하나만 둔다
  const toastTimer = useRef<number>();
  const showToast = useCallback((text: string, icon: IconName = 'info') => {
    setToast({ text, icon });
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 2000);
  }, []);
  useEffect(() => () => window.clearTimeout(toastTimer.current), []);

  /** 앞선 대화를 계약 형식으로 만든다. 최근 HISTORY_MAX_ITEMS개만 보낸다. */
  const history = (list: Turn[]): ChatTurnMessage[] => list.flatMap((t): ChatTurnMessage[] => {
    if (t.kind === 'prompt') return [{ role: 'assistant', text: t.text }];
    const out: ChatTurnMessage[] = [{ role: 'user', text: t.question }];
    if (t.response?.answer) out.push({ role: 'assistant', text: t.response.answer });
    return out;
  }).slice(-HISTORY_MAX_ITEMS);

  // 답변을 기다리는 질문. 같은 질문이 연달아 눌려 요청이 두 번 나가지 않게 한다 (LLM 호출 비용)
  const pending = useRef(new Set<string>());

  const run = useCallback(async (id: number, request: AskRequest) => {
    pending.current.add(request.question);
    try {
      const response = await askPolicy(request);
      setTurns((list) => list.map((t) => (t.id === id && t.kind === 'qa' ? { ...t, state: 'done', response } : t)));
    } catch (err) {
      const message = err instanceof Error ? err.message : '일시적인 문제가 생겼어요. 잠시 후 다시 시도해 주세요.';
      setTurns((list) => list.map((t) => (t.id === id && t.kind === 'qa' ? { ...t, state: 'error', error: message } : t)));
    } finally {
      pending.current.delete(request.question);
    }
  }, []);

  const requests = useRef(new Map<number, AskRequest>());

  /**
   * 질문 보내기: 질문 속 나이·지역·소득을 먼저 '내 조건'에 반영한다.
   * base: 이번 질문에 쓸 '내 조건'. 대표 상황 카드처럼 조건을 바꾸면서 바로 보낼 때 넘긴다(상태 반영을 기다리지 않게).
   */
  const ask = useCallback((raw: string, origin: HTMLElement | null, base: UserProfile = profile) => {
    const question = raw.trim();
    if (!question) return;
    if (pending.current.has(question)) { showToast('같은 질문의 답변을 기다리고 있어요'); return; }
    burst(origin, reduceMotion);

    const found = parseProfile(question, awaiting);
    const next: UserProfile = {
      age: found.age ?? base.age, region: found.region ?? base.region, income: found.income ?? base.income,
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
  }, [awaiting, profile, reduceMotion, run, showToast]);

  const retry = useCallback((id: number) => {
    const request = requests.current.get(id);
    if (!request || pending.current.has(request.question)) return;
    setTurns((list) => list.map((t) => (t.id === id && t.kind === 'qa' ? { ...t, state: 'loading', error: undefined } : t)));
    void run(id, request);
  }, [run]);

  /** 추가 질문 버튼: 정보 요청(ask_info)이면 AI가 먼저 되묻고, 일반 질문이면 바로 보낸다. */
  const followUp = useCallback((f: FollowUp, origin: HTMLElement) => {
    if (f.kind !== 'ask_info') { ask(f.text, origin); return; }
    const { label, example } = infoField(f.field);
    // 대답 속 값은 화면이 문장에서 읽어 profile에 넣고, 서버에는 어떤 항목의 대답인지만 알린다 (계약 §3.2)
    setAwaiting(f.field ?? null);
    setTurns((list) => [...list, { id: nextId.current++, kind: 'prompt', text: `좋아요! ${label}${josa(label)} 알려 주세요. 아래 입력창에 편하게 적어 주시면 그 조건으로 다시 확인해 드릴게요. (${example})` }]);
    setMiniPlaceholder(example);
    setTimeout(() => (miniOn ? miniInputRef : heroInputRef).current?.focus({ preventScroll: true }), 300);
  }, [ask, miniOn]);

  /** 대표 상황 카드: PRD 시나리오의 조건을 '내 조건'에 넣고 대표 질문을 바로 보낸다 (소득은 비운다). */
  const startPersona = useCallback((p: Persona, origin: HTMLElement | null) => {
    const base: UserProfile = { age: p.profile.age, region: p.profile.region, income: null };
    setAge(base.age != null ? String(base.age) : '');
    setRegion(base.region);
    setIncome(null);
    ask(p.question, origin, base);
  }, [ask]);

  /** 생애주기 화면의 '이 정책 물어보기': 상담 화면으로 넘어가 질문을 보내고, 그 답변 위치를 보여 준다 */
  const askFromOtherPage = useCallback((question: string) => {
    showLatestTurn.current = true;
    ask(question, null);
    location.hash = askHref;
  }, [ask]);

  /** 처음으로: 주소의 화면 표시(#)까지 지우고 새로 시작한다 */
  const restart = () => { window.history.replaceState(null, '', location.pathname + location.search); location.reload(); };

  const profileText = describeProfile(profile);
  const condText = profileText ? `내 조건 · ${profileText}` : '내 조건 없음';

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
          <nav className="tabs" aria-label="화면">
            {/* 좁은 화면에서는 로고가 가려지지 않게 '하기'·'정책'을 줄여 보여 준다 */}
            <a href={askHref} aria-current={onAskPage ? 'page' : undefined}>상담<span className="long">하기</span></a>
            <a href={lifeHref()} aria-current={route.page === 'life' ? 'page' : undefined}>생애주기</a>
            <a href={mapHref()} aria-current={route.page === 'map' ? 'page' : undefined}><span className="long">정책&nbsp;</span>관계 지도</a>
          </nav>
          <div className="right">
            <button className="ghost" type="button" onClick={restart}><span className="sp" aria-hidden="true">↺</span><span className="t">처음으로</span></button>
            <button className="ghost" type="button" aria-label="화면 테마 바꾸기" onClick={() => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))}><span className="sp" aria-hidden="true">◐</span><span className="t">테마</span></button>
          </div>
        </div>
      </header>

      {route.page !== 'ask' ? (
        <Suspense fallback={<section className="board"><div className="wrap map-msg" role="status">화면을 여는 중이에요…</div></section>}>
          {route.page === 'map'
            ? <PolicyMap policyId={route.policyId} />
            : <LifeTimeline stageName={route.stage} myAge={profile.age} onAsk={askFromOtherPage} />}
        </Suspense>
      ) : (<>
      <Hero age={age} ageError={ageError} region={region} onAge={setAge} onRegion={setRegion} categories={CATEGORIES}
        onAsk={ask} askBoxRef={askBoxRef} inputRef={heroInputRef} reduceMotion={reduceMotion} />

      <section className="board" id="board">
        <div className="wrap">
          <div className="board-h"><h2>상담 <span>결과</span></h2><p>답변마다 근거 출처를 함께 보여 드려요</p></div>
          {turns.length === 0 && (
            <PersonaStart personas={PERSONAS} onPersona={startPersona} />
          )}
          {turns.map((t, i) => (
            <div key={t.id} ref={i === turns.length - 1 ? lastTurnRef : undefined}>
              <TurnView turn={t} onFollowUp={followUp} onRetry={retry} onDemoApply={() => showToast('시연용 버튼이에요. 실제 신청 주소로 연결되지 않아요.')} sampleMode={usingSample} />
            </div>
          ))}
        </div>
      </section>
      </>)}

      <footer><div className="wrap">LifePolicy AI · SKN36 3차 프로젝트 학습용 시연. 최종 자격과 신청 가능 여부는 담당 기관의 공식 원문에서 확인해 주세요. 아이콘은 이 화면용으로 직접 그린 SVG예요.</div></footer>

      <BottomBar visible={onAskPage && miniOn} condText={condText} placeholder={miniPlaceholder} inputRef={miniInputRef} onAsk={ask}
        onCondClick={() => { scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' }); setTimeout(() => document.getElementById('age')?.focus(), 400); }} />
      <JumpButtons reduceMotion={reduceMotion} watch={turns} />
      <Toast message={toast} />
    </>
  );
}
