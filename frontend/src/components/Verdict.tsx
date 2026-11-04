import { fmtLocal } from "../lib/format";
import type { Plan } from "../types";

interface Props {
  plan: Plan;
  onApplyLeaveLater: (departureUtc: string) => void;
  busy: boolean;
}

const STATUS_TEXT = { go: "Clear to go", caution: "Go with caution", no_travel: "Hold departure" } as const;

export function Verdict({ plan, onApplyLeaveLater, busy }: Props) {
  const rec = plan.recommendation;
  const ll = plan.leave_later;
  return (
    <section className={`verdict ${rec.status}`} aria-live="polite">
      <p className="status">{STATUS_TEXT[rec.status]}</p>
      <h2>{rec.headline}</h2>
      {rec.reasons.length > 0 && (
        <ul className="reasons">
          {rec.reasons.map((r) => (
            <li key={r.versus}>
              <span className="vs">vs {r.versus}</span>
              {r.text.charAt(0).toUpperCase() + r.text.slice(1)}.
            </li>
          ))}
        </ul>
      )}
      {ll && (
        <div className="leave-later">
          <p>{ll.text}</p>
          <button className="secondary" disabled={busy} onClick={() => onApplyLeaveLater(ll.departure_utc)}>
            Plan for {fmtLocal(ll.departure_utc, plan.inputs.departure_tz)}
          </button>
        </div>
      )}
      <p className="meta muted small">
        Leaving {fmtLocal(plan.inputs.departure_utc, plan.inputs.departure_tz)} with {plan.inputs.load_lb.toLocaleString()} lb.
        Weather checked every {plan.inputs.interval_mi} mi at the truck's arrival time. Forecast: {plan.forecast_source},
        fetched {fmtLocal(plan.generated_utc, plan.inputs.departure_tz, false)}.
      </p>
      {plan.warnings.map((w) => (
        <p key={w} className="warning">
          {w}
        </p>
      ))}
    </section>
  );
}
