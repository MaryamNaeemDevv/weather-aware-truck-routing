import type { Route } from "../types";

export type GhostState = "before" | "driving" | "break" | "rest" | "arrived";

/** Where the truck is `minutes` after departure, using the route's time-indexed track. */
export function truckAt(
  route: Route,
  minutes: number,
  departureMs: number,
): { lng: number; lat: number; state: GhostState } {
  const t = route.track;
  if (minutes <= 0) return { lng: t[0][1], lat: t[0][2], state: "before" };
  const last = t[t.length - 1];
  if (minutes >= last[0]) return { lng: last[1], lat: last[2], state: "arrived" };

  for (const s of route.stops) {
    const start = (Date.parse(s.start_utc) - departureMs) / 60000;
    if (minutes >= start && minutes <= start + s.duration_s / 60) {
      return { lng: s.lng, lat: s.lat, state: s.kind };
    }
  }

  let lo = 0;
  let hi = t.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (t[mid][0] <= minutes) lo = mid;
    else hi = mid;
  }
  const a = t[lo];
  const b = t[hi];
  const span = b[0] - a[0];
  const f = span > 0 ? (minutes - a[0]) / span : 0;
  return { lng: a[1] + (b[1] - a[1]) * f, lat: a[2] + (b[2] - a[2]) * f, state: "driving" };
}
