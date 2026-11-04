import { useState } from "react";
import type { Place, PlanInput } from "../types";
import { PlaceInput } from "./PlaceInput";

interface Props {
  busy: boolean;
  fieldErrors?: Record<string, unknown>;
  onSubmit: (input: PlanInput) => void;
  initial?: PlanInput | null;
}

const EXAMPLE = {
  origin: { lat: 39.739, lng: -104.99, label: "Denver, CO" },
  destination: { lat: 41.878, lng: -87.63, label: "Chicago, IL" },
};

function firstMessage(v: unknown): string | undefined {
  if (!v) return undefined;
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return firstMessage(v[0]);
  if (typeof v === "object") return firstMessage(Object.values(v as object)[0]);
  return undefined;
}

export function TripForm({ busy, fieldErrors, onSubmit, initial }: Props) {
  const [origin, setOrigin] = useState<Place | null>(initial?.origin ?? null);
  const [destination, setDestination] = useState<Place | null>(initial?.destination ?? null);
  const [leaveNow, setLeaveNow] = useState(!initial?.departure_local);
  const [departure, setDeparture] = useState(initial?.departure_local ?? "");
  const [load, setLoad] = useState(String(initial?.load_lb ?? 38000));
  const [interval, setInterval] = useState<10 | 25 | 50>(initial?.interval_mi ?? 25);
  const [hos, setHos] = useState(initial?.hos_enabled ?? true);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const server = (k: string) => firstMessage(fieldErrors?.[k]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const next: Record<string, string> = {};
    if (!origin) next.origin = "Pick a starting place from the list.";
    if (!destination) next.destination = "Pick a destination from the list.";
    if (origin && destination && origin.lat === destination.lat && origin.lng === destination.lng)
      next.destination = "Destination is the same as the origin.";
    const loadNum = Number(load.replace(/,/g, ""));
    if (!Number.isFinite(loadNum) || loadNum <= 0) next.load = "Enter the cargo weight in pounds.";
    else if (loadNum > 120000) next.load = "That's more than any permitted load. Is it in pounds?";
    if (!leaveNow && !departure) next.departure = "Choose a departure time, or select Leave now.";
    setErrors(next);
    if (Object.keys(next).length) return;
    onSubmit({
      origin: origin!,
      destination: destination!,
      departure_local: leaveNow ? null : departure,
      load_lb: loadNum,
      interval_mi: interval,
      hos_enabled: hos,
    });
  };

  return (
    <form className="trip-form" onSubmit={submit} noValidate>
      <div className="od">
        <PlaceInput
          label="From"
          value={origin}
          onChange={setOrigin}
          placeholder="City, address or warehouse"
          error={errors.origin ?? server("origin")}
        />
        <PlaceInput
          label="To"
          value={destination}
          onChange={setDestination}
          placeholder="City, address or warehouse"
          error={errors.destination ?? server("destination")}
        />
      </div>

      <div className="row">
        <div className="field">
          <span className="label-text" id="dep-label">
            Departure
          </span>
          <div className="seg" role="radiogroup" aria-labelledby="dep-label">
            <button type="button" role="radio" aria-checked={leaveNow} onClick={() => setLeaveNow(true)}>
              Leave now
            </button>
            <button type="button" role="radio" aria-checked={!leaveNow} onClick={() => setLeaveNow(false)}>
              Later
            </button>
          </div>
        </div>
        <div className="field">
          <label htmlFor="load">Load weight (lb)</label>
          <input
            id="load"
            inputMode="numeric"
            value={load}
            onChange={(e) => setLoad(e.target.value)}
            aria-invalid={!!(errors.load ?? server("load_lb"))}
          />
        </div>
      </div>
      {!leaveNow && (
        <div className="field">
          <label htmlFor="dep">Local time at the origin</label>
          <input
            id="dep"
            type="datetime-local"
            value={departure}
            onChange={(e) => setDeparture(e.target.value)}
            aria-invalid={!!(errors.departure ?? server("departure_local"))}
          />
        </div>
      )}
      {(errors.load ?? server("load_lb")) && <p className="field-error">{errors.load ?? server("load_lb")}</p>}
      {(errors.departure ?? server("departure_local")) && (
        <p className="field-error">{errors.departure ?? server("departure_local")}</p>
      )}

      <div className="row">
        <div className="field">
          <span className="label-text" id="int-label">
            Check weather every
          </span>
          <div className="seg" role="radiogroup" aria-labelledby="int-label">
            {([10, 25, 50] as const).map((n) => (
              <button key={n} type="button" role="radio" aria-checked={interval === n} onClick={() => setInterval(n)}>
                {n} mi
              </button>
            ))}
          </div>
        </div>
        <label className="check">
          <input type="checkbox" checked={hos} onChange={(e) => setHos(e.target.checked)} />
          <span>
            Include required breaks
            <small>30 min after 8 h, 10 h rest after 11 h driving</small>
          </span>
        </label>
      </div>

      <div className="actions">
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Checking routes and weather…" : "Compare routes"}
        </button>
        {!origin && !destination && (
          <button
            type="button"
            className="link"
            onClick={() => {
              setOrigin(EXAMPLE.origin);
              setDestination(EXAMPLE.destination);
            }}
          >
            Try Denver to Chicago
          </button>
        )}
      </div>
    </form>
  );
}
