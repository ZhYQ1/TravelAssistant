export interface SessionContext {
  userId: string;
  sessionId: string;
}

export interface RecentTrip {
  planId: string;
  title: string;
  at: string;
}

const SESSION_KEY = "xingzhi.session";
const RECENT_KEY = "xingzhi.recent";

function readJSON<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function writeJSON(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 隐私模式或配额受限时静默降级，不影响页面使用。
  }
}

export function loadSession(): SessionContext {
  const existing = readJSON<SessionContext>(SESSION_KEY);
  if (existing && existing.userId && existing.sessionId) return existing;
  const fresh: SessionContext = {
    userId: `traveller-${crypto.randomUUID().slice(0, 8)}`,
    sessionId: `session-${crypto.randomUUID()}`,
  };
  writeJSON(SESSION_KEY, fresh);
  return fresh;
}

export function listRecentTrips(): RecentTrip[] {
  const items = readJSON<RecentTrip[]>(RECENT_KEY);
  if (!Array.isArray(items)) return [];
  return items.filter((item) => item && typeof item.planId === "string");
}

export function recordRecentTrip(trip: RecentTrip): void {
  const items = listRecentTrips().filter((item) => item.planId !== trip.planId);
  items.unshift(trip);
  writeJSON(RECENT_KEY, items.slice(0, 12));
}