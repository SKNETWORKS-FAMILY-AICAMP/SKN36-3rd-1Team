import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import type { PolicyCheck } from '../api/types';
import { safeHttpUrl } from '../lib/validate';
import { policyCalendar } from '../lib/policyCalendar';
import { useScheduleDay } from './ScheduleClock';

export const SAVED_POLICIES_KEY = 'ssok.saved-policies.v1';
export type SavedPolicy = Pick<PolicyCheck, 'policy_id' | 'policy_name' | 'source_url'>;
/** 개인정보·조건 판정·과거 일정은 저장하지 않는다. 외부 저장 값은 필요한 필드만 읽는다. */
export function readSavedPolicies(raw: string | null): SavedPolicy[] {
  try {
    const rows: unknown = JSON.parse(raw ?? '[]');
    if (!Array.isArray(rows)) return [];
    const seen = new Set<string>();
    return rows.flatMap(row => {
      if (!row || typeof row !== 'object') return [];
      const p = row as SavedPolicy;
      if (typeof p.policy_id !== 'string' || !p.policy_id.trim() || typeof p.policy_name !== 'string' || !p.policy_name.trim() || seen.has(p.policy_id)) return [];
      seen.add(p.policy_id);
      return [{ policy_id: p.policy_id, policy_name: p.policy_name, source_url: safeHttpUrl(p.source_url) ?? '' }];
    });
  } catch { return []; }
}
// 브라우저가 내려받기를 시작할 시간을 둔 뒤 임시 파일 주소를 해제한다
const DOWNLOAD_URL_REVOKE_MS = 1000;

/** 이 화면에서 방금 저장한 정책(다른 탭에서 바뀐 저장은 포함하지 않는다). seq는 같은 정책을 다시 저장해도 알림이 다시 뜨게 한다 */
export type SavedEvent = { policy: SavedPolicy; seq: number };
const SavedContext = createContext<{ saved: SavedPolicy[]; toggle: (p: SavedPolicy) => void; error: string; lastSaved: SavedEvent | null } | null>(null);
export const useSavedPolicies = () => useContext(SavedContext);
export function SavedPoliciesProvider({ children }: { children: ReactNode }) {
  const [saved, setSaved] = useState<SavedPolicy[]>(() => {
    try { return readSavedPolicies(localStorage.getItem(SAVED_POLICIES_KEY)); } catch { return []; }
  });
  const [error, setError] = useState('');
  const [lastSaved, setLastSaved] = useState<SavedEvent | null>(null);
  useEffect(() => {
    const sync = (e: StorageEvent) => { if (e.storageArea === localStorage && (e.key === SAVED_POLICIES_KEY || e.key === null)) setSaved(readSavedPolicies(e.newValue)); };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, []);
  function toggle(p: SavedPolicy) {
    const removing = saved.some(x => x.policy_id === p.policy_id);
    const entry = { policy_id: p.policy_id, policy_name: p.policy_name, source_url: safeHttpUrl(p.source_url) ?? '' };
    const next = removing ? saved.filter(x => x.policy_id !== p.policy_id) : [...saved, entry];
    try {
      localStorage.setItem(SAVED_POLICIES_KEY, JSON.stringify(next)); setError(''); setSaved(next);
      if (!removing) setLastSaved(prev => ({ policy: entry, seq: (prev?.seq ?? 0) + 1 }));
    }
    catch { setError('브라우저에 저장하지 못했어요. 저장 공간과 브라우저 설정을 확인해 주세요.'); }
  }
  return <SavedContext.Provider value={{ saved, toggle, error, lastSaved }}>{children}</SavedContext.Provider>;
}
/** show로 저장 버튼과 캘린더 버튼을 카드의 서로 다른 자리에 나눠 그릴 수 있다 */
export function PolicyPersonalActions({ policy, sample = false, show = 'all' }: { policy: PolicyCheck; sample?: boolean; show?: 'all' | 'save' | 'calendar' }) {
  const state = useSavedPolicies();
  const today = useScheduleDay();
  const [error, setError] = useState('');
  if (sample) return null;
  const saved = state?.saved.some(p => p.policy_id === policy.policy_id) ?? false;
  const calendar = policyCalendar(policy, today);
  function download() {
    if (!calendar) return;
    let url: string | null = null;
    try {
      url = URL.createObjectURL(new Blob([calendar], { type: 'text/calendar;charset=utf-8' }));
      const link = document.createElement('a');
      link.href = url; link.download = 'ssok-deadline.ics'; document.body.append(link); link.click(); link.remove();
      const fileUrl = url; window.setTimeout(() => URL.revokeObjectURL(fileUrl), DOWNLOAD_URL_REVOKE_MS); setError('');
    } catch { if (url) URL.revokeObjectURL(url); setError('캘린더 파일을 만들지 못했어요. 다시 시도해 주세요.'); }
  }
  const showSave = show !== 'calendar', showCalendar = show !== 'save' && !!calendar;
  return <>{showSave && state && <button type="button" className="btn btn-o" aria-pressed={saved} onClick={() => state.toggle(policy)}>{saved ? '★ 저장 해제' : '☆ 관심 정책 저장'}</button>}
    {showCalendar && <button type="button" className={show === 'calendar' ? 'calendar-link' : 'btn btn-o'} onClick={download}>캘린더에 추가</button>}
    {showCalendar && <small className="calendar-note">종일 일정 · 알림은 캘린더 설정에 따라요</small>}
    {error && <span role="alert">{error}</span>}</>;
}
