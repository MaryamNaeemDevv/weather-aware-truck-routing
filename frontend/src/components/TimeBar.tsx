import { useEffect, useRef, useState } from "react";
import { fmtLocal, LEVELS, UNKNOWN } from "../lib/format";
import { useUi } from "../store";
import type { HeatLayer, Heatmap, Plan } from "../types";

const LAYERS: { id: HeatLayer; label: string }[] = [
  { id: "overall", label: "All" },
  { id: "wind", label: "Wind" },
  { id: "rain", label: "Rain" },
  { id: "snow", label: "Snow" },
];

interface Props {
  plan: Plan | null;
  heatmap: Heatmap | null;
  loading: boolean;
  error: string | null;
}

export function TimeBar({ plan, heatmap, loading, error }: Props) {
  const { hour, setHour, layer, setLayer, showHeatmap, toggleHeatmap } = useUi();
  const [playing, setPlaying] = useState(false);
  const timer = useRef<number | null>(null);
  const reduceMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  useEffect(() => {
    if (!playing) return;
    timer.current = window.setInterval(() => {
      const h = useUi.getState().hour;
      if (h >= 48) {
        setPlaying(false);
        return;
      }
      setHour(h + 1);
    }, reduceMotion ? 900 : 350);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [playing, setHour, reduceMotion]);

  if (!plan) return null;
  const tz = plan.inputs.departure_tz;
  const startMs = heatmap ? Date.parse(heatmap.start_utc) : Date.now();
  const at = new Date(startMs + hour * 3600_000).toISOString();
  const depHour = (Date.parse(plan.inputs.departure_utc) - startMs) / 3600_000;
  const depPct = Math.max(0, Math.min(100, (depHour / 48) * 100));

  return (
    <div className="timebar" role="group" aria-label="Forecast time">
      <div className="timebar-top">
        <div className="time-readout">
          <strong>{fmtLocal(at, tz)}</strong>
          <span className="muted">{hour === 0 ? "now" : `+${hour} h`}</span>
        </div>
        <div className="seg small" role="radiogroup" aria-label="Heatmap layer">
          {LAYERS.map((l) => (
            <button key={l.id} role="radio" aria-checked={layer === l.id} onClick={() => setLayer(l.id)}>
              {l.label}
            </button>
          ))}
        </div>
        <label className="check inline">
          <input type="checkbox" checked={showHeatmap} onChange={toggleHeatmap} />
          <span>Heatmap</span>
        </label>
      </div>
      <div className="timebar-slider">
        <button
          className="play"
          onClick={() => {
            if (hour >= 48) setHour(0);
            setPlaying((p) => !p);
          }}
          aria-label={playing ? "Pause forecast animation" : "Play forecast animation"}
          disabled={!heatmap}
        >
          {playing ? (
            <svg viewBox="0 0 16 16" aria-hidden><rect x="3" y="2" width="3.5" height="12" rx="1" /><rect x="9.5" y="2" width="3.5" height="12" rx="1" /></svg>
          ) : (
            <svg viewBox="0 0 16 16" aria-hidden><path d="M4 2.5v11l9.5-5.5z" /></svg>
          )}
        </button>
        <div className="slider-wrap">
          <input
            type="range"
            min={0}
            max={48}
            step={1}
            value={hour}
            onChange={(e) => {
              setPlaying(false);
              setHour(Number(e.target.value));
            }}
            aria-label="Hours from now"
            aria-valuetext={fmtLocal(at, tz)}
          />
          {depHour >= 0 && depHour <= 48 && (
            <span className="dep-mark" style={{ left: `${depPct}%` }} title="Departure">
              Depart
            </span>
          )}
          <div className="slider-scale" aria-hidden>
            <span>Now</span>
            <span>+12 h</span>
            <span>+24 h</span>
            <span>+36 h</span>
            <span>+48 h</span>
          </div>
        </div>
      </div>
      <div className="legend" aria-label="Risk legend">
        {[...LEVELS, UNKNOWN].map((l) => (
          <span key={l.key}>
            <i style={{ background: l.color }} aria-hidden />
            {l.label}
          </span>
        ))}
        {loading && <span className="muted">Loading corridor forecast…</span>}
        {error && <span className="warning-inline">{error}</span>}
      </div>
    </div>
  );
}
