import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { kstDay } from '../lib/applicationSchedule';
const DAY_MS = 24 * 60 * 60 * 1000;
const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
const DayContext = createContext<string | null>(null);
export const useScheduleDay = () => useContext(DayContext) ?? kstDay();
export function ScheduleClock({ children }: { children: ReactNode }) {
  const [day, setDay] = useState(kstDay);
  useEffect(() => {
    const refresh = () => setDay(kstDay());
    const untilMidnight = DAY_MS - ((Date.now() + KST_OFFSET_MS) % DAY_MS);
    const timer = window.setTimeout(refresh, untilMidnight);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
    return () => { clearTimeout(timer); window.removeEventListener('focus', refresh); document.removeEventListener('visibilitychange', refresh); };
  }, [day]);
  return <DayContext.Provider value={day}>{children}</DayContext.Provider>;
}
