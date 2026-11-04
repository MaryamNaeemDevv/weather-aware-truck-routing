import { useMutation, useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { CheckpointTable } from "./components/CheckpointTable";
import { MapView } from "./components/MapView";
import { RouteCard } from "./components/RouteCard";
import { TimeBar } from "./components/TimeBar";
import { TripForm } from "./components/TripForm";
import { Verdict } from "./components/Verdict";
import { api, ApiError } from "./lib/api";
import { toLocalInput } from "./lib/format";
import { useUi } from "./store";
import type { Plan, PlanInput } from "./types";

export default function App() {
  const [input, setInput] = useState<PlanInput | null>(null);
  const [formKey, setFormKey] = useState(0);
  const abort = useRef<AbortController | null>(null);
  const selected = useUi((s) => s.selectedRoute);

  const plan = useMutation<Plan, Error, PlanInput>({
    mutationFn: (body) => {
      abort.current?.abort(); // a newer request always wins
      const ctrl = new AbortController();
      abort.current = ctrl;
      return api.plan(body, ctrl.signal);
    },
    onSuccess: (p) => {
      const ui = useUi.getState();
      ui.select(null);
      const depOffset = Math.round((Date.parse(p.inputs.departure_utc) - Date.now()) / 3600_000);
      ui.setHour(Math.max(0, Math.min(48, depOffset)));
    },
  });

  const data = plan.data ?? null;
  const heat = useQuery({
    queryKey: ["heatmap", data?.plan_id],
    queryFn: ({ signal }) => api.heatmap(data!.plan_id, signal),
    enabled: !!data,
    staleTime: Infinity,
    retry: 1,
  });

  const run = (body: PlanInput) => {
    setInput(body);
    plan.mutate(body);
  };

  const applyLeaveLater = (departureUtc: string) => {
    if (!input || !data) return;
    const next = { ...input, departure_local: toLocalInput(departureUtc, data.inputs.departure_tz) };
    setFormKey((k) => k + 1);
    run(next);
  };

  const err = plan.error instanceof ApiError ? plan.error : plan.error ? new ApiError(0, "error", plan.error.message) : null;
  const shownErr = err && err.name !== "AbortError" ? err : null;
  const ranking = data?.recommendation.ranking ?? [];
  const ordered = data ? ranking.map((id) => data.routes.find((r) => r.id === id)!) : [];
  const focusRoute = data?.routes.find((r) => r.id === (selected ?? data.recommendation.route_id));

  return (
    <div className="app">
      <aside className="panel">
        <header className="brand">
          <h1>Weather-aware truck routing</h1>
          <p>Compare three routes by the weather the truck will actually meet, mile by mile.</p>
        </header>

        <TripForm
          key={formKey}
          initial={input}
          busy={plan.isPending}
          fieldErrors={shownErr?.fields}
          onSubmit={run}
        />

        {shownErr && !shownErr.fields && (
          <p className="error-box" role="alert">
            {shownErr.message}
          </p>
        )}

        {data?.mode === "demo" && (
          <p className="demo-note">
            Demo data: routes and weather are synthetic because no API keys are configured on this server.
          </p>
        )}

        {plan.isPending && !data && <div className="skeleton" aria-hidden />}

        {data && (
          <div className={`results${plan.isPending ? " stale" : ""}`}>
            <Verdict plan={data} onApplyLeaveLater={applyLeaveLater} busy={plan.isPending} />
            <div className="routes">
              {ordered.map((r, i) => (
                <RouteCard key={r.id} route={r} rank={i + 1} recommended={r.id === data.recommendation.route_id} />
              ))}
            </div>
            {focusRoute && <CheckpointTable route={focusRoute} />}
          </div>
        )}

        {!data && !plan.isPending && (
          <div className="intro muted">
            <p>
              Each route is checked at regular mile marks, using the forecast for the hour the truck reaches that point,
              with required driver breaks included.
            </p>
            <p>The safest route wins: fewest No Travel and Severe miles first, then High miles, average risk and time.</p>
          </div>
        )}
      </aside>

      <main className="stage">
        <MapView plan={data} heatmap={heat.data ?? null} />
        <TimeBar
          plan={data}
          heatmap={heat.data ?? null}
          loading={heat.isFetching}
          error={heat.error ? "Corridor forecast unavailable. Routes and checkpoints are still accurate." : null}
        />
      </main>
    </div>
  );
}
