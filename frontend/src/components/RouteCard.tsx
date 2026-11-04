import { ADVISORY_LABEL, fmtDuration, fmtLocal, fmtMiles, LEVELS, level } from "../lib/format";
import { useUi } from "../store";
import type { Route, WorstStretch } from "../types";
import { RiskRibbon } from "./RiskRibbon";

function riskLine(route: Route) {
  const s = route.summary.miles_by_level;
  const parts = (
    [
      [4, s.no_travel],
      [3, s.severe],
      [2, s.high],
      [1, s.moderate],
    ] as const
  )
    .filter(([, mi]) => mi > 0)
    .map(([code, mi]) => ({ code, text: `${LEVELS[code].label} ${fmtMiles(mi)}` }));
  if (route.summary.unknown_mi > 0) parts.push({ code: -1 as never, text: `No forecast ${fmtMiles(route.summary.unknown_mi)}` });
  return parts;
}

function stretchText(w: WorstStretch) {
  const lv = level(w.level).label;
  const factor = w.factors.join(" and ") || "weather";
  const peaks: string[] = [];
  if (w.factors.includes("wind") && w.peak.wind_mph != null) peaks.push(`${Math.round(w.peak.wind_mph)} mph`);
  if (w.factors.includes("rain") && w.peak.rain_in != null) peaks.push(`${w.peak.rain_in.toFixed(2)} in/hr`);
  if (w.factors.includes("snow") && w.peak.snow_in != null) peaks.push(`${w.peak.snow_in.toFixed(1)} in/hr snow`);
  const where = w.place ? ` ${w.place.startsWith("near") || /mi from/.test(w.place) ? "" : "near "}${w.place}` : "";
  const when =
    w.start_utc === w.end_utc
      ? fmtLocal(w.start_utc, w.tz)
      : `${fmtLocal(w.start_utc, w.tz)} to ${fmtLocal(w.end_utc, w.tz, false)}`;
  return {
    title: `${lv} ${factor}${peaks.length ? ` (${peaks.join(", ")})` : ""}`,
    detail: `${where.trim() ? `${where.trim()}, ` : ""}miles ${Math.round(w.start_mile)}–${Math.round(w.end_mile)}, ${when}`,
  };
}

interface Props {
  route: Route;
  recommended: boolean;
  rank: number;
}

export function RouteCard({ route, recommended, rank }: Props) {
  const selected = useUi((s) => s.selectedRoute);
  const select = useUi((s) => s.select);
  const isSelected = selected ? selected === route.id : recommended;
  const risks = riskLine(route);
  const stops = route.stops.length;
  const stopTime = route.elapsed_s - route.drive_s;
  const worst = route.worst_stretch ? stretchText(route.worst_stretch) : null;

  return (
    <article
      className={`route-card${isSelected ? " selected" : ""}${recommended ? " recommended" : ""}`}
      aria-current={isSelected}
    >
      <button className="route-hit" onClick={() => select(route.id)} aria-label={`Show route ${route.id} on the map`} />
      <header>
        <span className="shield" aria-hidden>
          {route.id}
        </span>
        <div className="route-title">
          <h3>
            Route {route.id}
            {recommended && <span className="badge rec">Recommended</span>}
            {route.flags.includes("detour") && <span className="badge">Long detour</span>}
          </h3>
          <p className="muted">
            {rank === 1 ? "Ranked first" : `Ranked ${rank === 2 ? "second" : "third"}`} by weather risk, then time
          </p>
        </div>
      </header>

      <dl className="figures">
        <div>
          <dt>Distance</dt>
          <dd>{fmtMiles(route.distance_mi)}</dd>
        </div>
        <div>
          <dt>Driving</dt>
          <dd>{fmtDuration(route.drive_s)}</dd>
        </div>
        <div>
          <dt>Door to door</dt>
          <dd>{fmtDuration(route.elapsed_s)}</dd>
        </div>
        <div>
          <dt>Arrive</dt>
          <dd>{fmtLocal(route.arrival_utc, route.arrival_tz)}</dd>
        </div>
      </dl>

      <RiskRibbon route={route} />

      <p className="risk-line">
        {risks.length === 0 ? (
          <span>Low risk the whole way.</span>
        ) : (
          risks.map((r) => (
            <span key={r.text} className="risk-chip">
              <i style={{ background: level(r.code).color }} aria-hidden />
              {r.text}
            </span>
          ))
        )}
      </p>

      {worst && (
        <p className="worst">
          <b>Worst stretch: {worst.title}.</b> {worst.detail}.
        </p>
      )}
      {stops > 0 && (
        <p className="muted small">
          {stops} required stop{stops > 1 ? "s" : ""} add {fmtDuration(stopTime)}. Weather while parked isn't counted as road risk.
        </p>
      )}
      {route.advisories.length > 0 && (
        <p className="advisories">
          {route.advisories.map((a) => (
            <span key={a} className="advisory">
              {ADVISORY_LABEL[a] ?? a}
            </span>
          ))}
          <span className="muted small">Shown for awareness; not part of the risk score.</span>
        </p>
      )}
      {route.notices.filter((n) => !n.startsWith("Demo")).map((n) => (
        <p key={n} className="muted small">
          {n}
        </p>
      ))}
    </article>
  );
}
