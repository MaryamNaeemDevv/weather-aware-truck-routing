import type { LevelCode } from "../types";

export const LEVELS = [
  { code: 0, key: "low", label: "Low", color: "#2F9E6B" },
  { code: 1, key: "moderate", label: "Moderate", color: "#E2B714" },
  { code: 2, key: "high", label: "High", color: "#EE7D1E" },
  { code: 3, key: "severe", label: "Severe", color: "#CF3A2A" },
  { code: 4, key: "no_travel", label: "No Travel", color: "#6E1F6A" },
] as const;

export const UNKNOWN = { code: -1, key: "unknown", label: "No forecast", color: "#9AA6B0" } as const;

export function level(code: number | null | undefined) {
  if (code === null || code === undefined || code < 0) return UNKNOWN;
  return LEVELS[Math.min(code, 4)];
}

export function levelLabel(code: LevelCode | number | null) {
  return level(code).label;
}

export function fmtDuration(seconds: number) {
  const mins = Math.round(seconds / 60);
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  if (!h) return `${m} min`;
  return m ? `${h}h ${String(m).padStart(2, "0")}m` : `${h}h`;
}

export function fmtMiles(mi: number) {
  return mi >= 100 ? `${Math.round(mi).toLocaleString()} mi` : `${mi.toFixed(mi < 10 ? 1 : 0)} mi`;
}

const dtfCache = new Map<string, Intl.DateTimeFormat>();
function dtf(tz: string, withDay: boolean) {
  const key = `${tz}|${withDay}`;
  let f = dtfCache.get(key);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      weekday: withDay ? "short" : undefined,
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
    dtfCache.set(key, f);
  }
  return f;
}

/** "Thu 3:05 PM MST" in the given IANA zone. */
export function fmtLocal(iso: string, tz: string, withDay = true) {
  return dtf(tz, withDay).format(new Date(iso));
}

/** Wall-clock "YYYY-MM-DDTHH:MM" for an instant in an IANA zone (for datetime-local inputs). */
export function toLocalInput(iso: string | Date, tz: string) {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(d)
      .map((p) => [p.type, p.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`;
}

export const FACTOR_LABEL: Record<string, string> = { wind: "wind", rain: "rain", snow: "snow" };

export const ADVISORY_LABEL: Record<string, string> = {
  freezing_rain: "Freezing rain",
  low_visibility: "Low visibility",
  strong_gusts: "Strong gusts",
};

export function fmtRate(v: number | null | undefined, unit: string, digits = 2) {
  return v === null || v === undefined ? "—" : `${v.toFixed(digits)} ${unit}`;
}
