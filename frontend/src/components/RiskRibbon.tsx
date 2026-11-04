import { fmtMiles, level } from "../lib/format";
import { useUi } from "../store";
import type { Route } from "../types";

/** Straight-line diagram of the route: one band per checkpoint span, coloured by risk,
 *  with required stops marked on top. */
export function RiskRibbon({ route, compact = false }: { route: Route; compact?: boolean }) {
  const hover = useUi((s) => s.hoverCheckpoint);
  const select = useUi((s) => s.select);
  const total = route.distance_mi || 1;
  const s = route.summary.miles_by_level;
  const aria =
    `Route ${route.id} risk along ${fmtMiles(route.distance_mi)}: ` +
    [
      ["No Travel", s.no_travel],
      ["Severe", s.severe],
      ["High", s.high],
      ["Moderate", s.moderate],
      ["Low", s.low],
      ["No forecast", route.summary.unknown_mi],
    ]
      .filter(([, v]) => (v as number) > 0)
      .map(([k, v]) => `${k} ${Math.round(v as number)} miles`)
      .join(", ");

  return (
    <div className={`ribbon${compact ? " compact" : ""}`} role="img" aria-label={aria}>
      <div className="ribbon-track">
        {route.checkpoints.map((cp) => {
          const lv = level(cp.level);
          const left = (cp.span[0] / total) * 100;
          const width = ((cp.span[1] - cp.span[0]) / total) * 100;
          return (
            <span
              key={cp.i}
              className={`band lv${cp.level}`}
              style={{ left: `${left}%`, width: `calc(${width}% + 1px)`, background: lv.color }}
              onMouseEnter={() => hover({ route: route.id, i: cp.i })}
              onMouseLeave={() => hover(null)}
              onClick={() => select(route.id)}
            />
          );
        })}
        {route.stops.map((st) => (
          <span
            key={st.start_utc}
            className={`stop-tick ${st.kind}`}
            style={{ left: `${(st.mile / total) * 100}%` }}
            title={st.kind === "rest" ? "10-hour rest" : "30-minute break"}
          />
        ))}
      </div>
      {!compact && (
        <div className="ribbon-scale">
          <span>0</span>
          <span>{fmtMiles(route.distance_mi)}</span>
        </div>
      )}
    </div>
  );
}
