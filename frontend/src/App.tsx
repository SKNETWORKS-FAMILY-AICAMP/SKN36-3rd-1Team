import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { askPolicy, usingSample } from './api/client';
import type { AskRequest, ChatTurnMessage, FollowUp, UserProfile } from './api/types';
import { CATEGORIES } from './config/categories';
import { infoField } from './config/infoFields';
import { PERSONAS, type Persona } from './config/personas';
import { HISTORY_MAX_ITEMS, QUESTION_MAX_LENGTH } from './config/request';
import { describeProfile, readProfileReply, REGIONS } from './lib/parseProfile';
import { planProfileReply } from './lib/profileReply';
import { askHref, lifeHref, mapHref, useRoute } from './lib/route';
import { recentPoliciesFromTurns } from './lib/recentPolicies';
import { AGE_MAX, checkAge } from './lib/validate';
import { Hero } from './components/Hero';
import { TurnView, type Turn } from './components/TurnView';
import { PersonaStart } from './components/PersonaStart';
import { ErrorBoundary } from './components/ErrorBoundary';
import { Logo } from './components/Logo';
import { BottomBar, JumpButtons, Toast, burst } from './components/Floating';
import type { IconName } from './components/Icon';

// 관계 지도는 그 페이지를 열 때만 코드와 데이터를 내려받는다
const PolicyMap = lazy(() => import('./components/PolicyMap'));
const LifeTimeline = lazy(() => import('./components/LifeTimeline'));


/** 중복 요청 판단용 질문 키: 앞뒤 공백을 빼고 연속 공백을 하나로 줄인다 */
const pendingKey = (question: string) => question.trim().replace(/\s+/g, ' ');

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
  const [awaiting, setAwaiting] = useState<{ field: string; question: string; turnId: number } | null>(null);
  const [toast, setToast] = useState<{ text: string; icon: IconName } | null>(null);
  const [miniOn, setMiniOn] = useState(false);

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
  // 띄어쓰기만 다른 질문도 같은 질문으로 본다 (예: '청년  월세' / '청년 월세').
  const pending = useRef(new Set<string>());
  const isPending = useCallback((question: string) => pending.current.has(pendingKey(question)), []);

  const run = useCallback(async (id: number, request: AskRequest) => {
    pending.current.add(pendingKey(request.question));
    try {
      const response = await askPolicy(request);
      setTurns((list) => list.map((t) => (t.id === id && t.kind === 'qa' ? { ...t, state: 'done', response } : t)));
    } catch (err) {
      const message = err instanceof Error ? err.message : '일시적인 문제가 생겼어요. 잠시 후 다시 시도해 주세요.';
      setTurns((list) => list.map((t) => (t.id === id && t.kind === 'qa' ? { ...t, state: 'error', error: message } : t)));
    } finally {
      pending.current.delete(pendingKey(request.question));
    }
  }, []);

  const requests = useRef(new Map<number, AskRequest>());

  /** 조건 값을 화면의 '내 조건' 입력값에 반영한다. 조건 답과 조건 입력칸이 함께 쓴다. */
  const applyProfileState = useCallback((values: Partial<UserProfile>) => {
    if (values.age != null) setAge(String(values.age));
    if (values.region) setRegion(values.region);
    if (values.income != null) setIncome(values.income);
  }, []);

  const ask = useCallback((raw: string, origin: HTMLElement | null, next: UserProfile = profile, field: string | null = null) => {
    let question = raw.trim();
    if (!question) return false;

    // "난 26살이야"처럼 조건만 알려 주는 답은 새 질문으로 검색하지 않는다 (#64).
    // 내 조건에 넣고, 바로 앞 질문을 바뀐 조건으로 다시 보낸다 (조건 입력칸으로 답한 것과 같은 동작).
    // 앞 질문의 답변을 기다리는 중이면 내 조건도 바꾸지 않는다.
    const reply = field == null ? readProfileReply(question) : null;
    if (reply) {
      const previous = [...turnsRef.current].reverse().find((t) => t.kind === 'qa');
      const plan = planProfileReply(reply, previous?.kind === 'qa' ? previous.question : null, next, isPending);
      if (plan.kind === 'busy') { showToast('같은 질문의 답변을 기다리고 있어요'); return false; }
      applyProfileState(reply);
      if (plan.kind === 'store') {
        showToast(`내 조건에 넣었어요: ${describeProfile(reply)}. 궁금한 정책을 물어봐 주세요`);
        return true;
      }
      showToast(`${describeProfile(reply)} 조건으로 앞 질문을 다시 확인할게요`);
      ({ question, profile: next, field } = plan);
    }
    if ([...question].length > QUESTION_MAX_LENGTH) {
      showToast(`질문은 ${QUESTION_MAX_LENGTH.toLocaleString('ko-KR')}자까지 입력할 수 있어요.`);
      return false;
    }
    if (isPending(question)) { showToast('같은 질문의 답변을 기다리고 있어요'); return false; }
    burst(origin, reduceMotion);

    const id = nextId.current++;
    const tags: { icon: IconName; text: string }[] = [];
    if (next.age != null) tags.push({ icon: 'cake', text: `${next.age}세` });
    if (next.region) tags.push({ icon: 'pin', text: next.region });

    const request: AskRequest = { question, profile: next, history: history(turnsRef.current), awaiting_field: field };
    requests.current.set(id, request);
    setTurns((list) => [...list, { id, kind: 'qa', question, tags, state: 'loading' }]);
    void run(id, request);
    setAwaiting(null);
    return true;
  }, [profile, reduceMotion, run, showToast, applyProfileState]);

  const retry = useCallback((id: number) => {
    const request = requests.current.get(id);
    if (!request || isPending(request.question)) return;
    setTurns((list) => list.map((t) => (t.id === id && t.kind === 'qa' ? { ...t, state: 'loading', error: undefined } : t)));
    void run(id, request);
  }, [run]);

  const followUp = (f: FollowUp, origin: HTMLElement, question: string, turnId: number) => {
    if (f.kind === 'question') { ask(f.text, origin); return; }
    if (f.field === 'age' || f.field === 'region' || f.field === 'income') {
      setAwaiting({ field: f.field, question, turnId });
    }
  };

  /** 대표 상황 카드: PRD 시나리오의 조건을 '내 조건'에 넣고 대표 질문을 바로 보낸다 (소득은 비운다). */
  const startPersona = useCallback((p: Persona, origin: HTMLElement | null) => {
    const base: UserProfile = { age: p.profile.age, region: p.profile.region, income: null };
    setAge(base.age != null ? String(base.age) : '');
    setRegion(base.region);
    setIncome(null);
    ask(p.question, origin, base);
  }, [ask]);

  /** 생애주기·관계 지도의 '물어보기': 상담 화면으로 넘어가 질문을 보내고, 그 답변 위치를 보여 준다 */
  const askFromOtherPage = useCallback((question: string) => {
    showLatestTurn.current = true;
    ask(question, null);
    location.hash = askHref;
  }, [ask]);

  /** 처음으로: 주소의 화면 표시(#)까지 지우고 새로 시작한다 */
  const restart = () => { window.history.replaceState(null, '', location.pathname + location.search); location.reload(); };

  // 상담에서 가장 최근에 나온 정책: 생애주기·관계 지도가 이어서 보여 준다
  const recent = useMemo(() => recentPoliciesFromTurns(turns), [turns]);

  const profileText = describeProfile(profile);
  const condText = profileText ? `내 조건 · ${profileText}` : '내 조건 없음';

  return (
    <>
      <div className="util">
        <div className="wrap"><span className="dot" /><span className="ut">학습용 시연 화면이에요{usingSample && <> · <b>예시 응답</b>이 표시돼요</>}</span></div>
      </div>
      <header className="gnb">
        <div className="wrap">
          <a className="logo" href={askHref} aria-label="SSOK 홈"><Logo /></a>
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
        <ErrorBoundary resetKey={location.hash}>
          <Suspense fallback={<section className="board"><div className="wrap map-msg" role="status">화면을 여는 중이에요…</div></section>}>
            {route.page === 'map'
              ? <PolicyMap policyId={route.policyId} recent={recent} reduceMotion={reduceMotion} onAsk={askFromOtherPage} />
              : <LifeTimeline stageName={route.stage} myAge={profile.age} onAsk={askFromOtherPage} recent={recent} />}
          </Suspense>
        </ErrorBoundary>
      ) : (<>
      <Hero age={age} ageError={ageError} region={region} income={income} onIncome={setIncome} onAge={setAge} onRegion={setRegion} categories={CATEGORIES}
        onAsk={ask} askBoxRef={askBoxRef} inputRef={heroInputRef} reduceMotion={reduceMotion} />

      <section className="board" id="board">
        <div className="wrap">
          <div className="board-h"><h2>상담 <span>결과</span></h2><p>답변마다 근거 출처를 함께 보여 드려요</p></div>
          {turns.length === 0 && (
            <PersonaStart personas={PERSONAS} onPersona={startPersona} />
          )}
          {turns.map((t, i) => (
            <div key={t.id} ref={i === turns.length - 1 ? lastTurnRef : undefined}>
              <TurnView turn={t} onFollowUp={(f, origin) => { if (t.kind === 'qa') followUp(f, origin, t.question, t.id); }} onRetry={retry} onDemoApply={() => showToast('시연용 버튼이에요. 실제 신청 주소로 연결되지 않아요.')} sampleMode={usingSample}
                onAskPolicy={(question, origin) => { ask(question, origin); }} />
            </div>
          ))}
          {awaiting && (
            <form key={awaiting.turnId + ":" + awaiting.field} className="conds-in" onSubmit={(event) => {
              event.preventDefault();
              const value = String(new FormData(event.currentTarget).get('condition') ?? '');
              if (!value) return;
              const { field, question } = awaiting;
              const numeric = Number(value);
              const next = { ...profile };
              if (field === 'age') {
                const parsedAge = checkAge(String(numeric));
                if (!parsedAge.ok || parsedAge.value == null) return;
                next.age = parsedAge.value;
              } else if (field === 'region') {
                next.region = value;
              } else {
                if (!Number.isFinite(numeric) || numeric < 0) return;
                next.income = numeric;
              }
              if (!ask(question, null, next, field)) return;
              applyProfileState(field === 'age' ? { age: next.age } : field === 'region' ? { region: next.region } : { income: next.income });
            }}>
              <label htmlFor="follow-up-condition">{infoField(awaiting.field).label}{awaiting.field === 'income' ? ' (만 원)' : ''}</label>
              {awaiting.field === 'region' ? (
                <select autoFocus required id="follow-up-condition" name="condition" defaultValue="">
                  <option value="">선택</option>
                  {REGIONS.map((value) => <option key={value}>{value}</option>)}
                </select>
              ) : (
                <input autoFocus required id="follow-up-condition" name="condition" type="number" min={0}
                  max={awaiting.field === 'age' ? AGE_MAX : undefined} step={awaiting.field === 'age' ? 1 : 'any'} />
              )}
              <button type="submit">다시 확인</button>
              <button type="button" onClick={() => setAwaiting(null)}>취소</button>
            </form>
          )}
        </div>
      </section>
      </>)}

      <footer><div className="wrap">SSOK · SKN36 3차 프로젝트 학습용 시연. 최종 자격과 신청 가능 여부는 담당 기관의 공식 원문에서 확인해 주세요.</div></footer>

      <BottomBar visible={onAskPage && miniOn} condText={condText} placeholder="이어서 물어보세요" inputRef={miniInputRef} onAsk={ask}
        onCondClick={() => { scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' }); setTimeout(() => document.getElementById('age')?.focus(), 400); }} />
      <JumpButtons reduceMotion={reduceMotion} watch={turns} />
      <Toast message={toast} />
    </>
  );
}
