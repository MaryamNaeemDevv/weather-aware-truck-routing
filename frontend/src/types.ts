// Mirrors the Django API payloads (backend/apps/planner/services/planner.py).

export type LevelCode = -1 | 0 | 1 | 2 | 3 | 4;

export interface Place {
  lat: number;
  lng: number;
  label: string;
}

export interface Checkpoint {
  i: number;
  mile: number;
  lat: number;
  lng: number;
  span: [number, number];
  eta_utc: string;
  tz: string;
  level: LevelCode;
  levels: { wind: LevelCode; rain: LevelCode; snow: LevelCode };
  drivers: string[];
  load_escalated: boolean;
  advisories: string[];
  wx: {
    wind_mph: number | null;
    gust_mph: number | null;
    rain_in: number | null;
    snow_in: number | null;
    temp_f: number | null;
    visibility_m: number | null;
  };
}

export interface Stop {
  kind: "break" | "rest";
  mile: number;
  lat: number;
  lng: number;
  start_utc: string;
  end_utc: string;
  duration_s: number;
  tz: string;
}

export interface RouteSummary {
  miles_by_level: { low: number; moderate: number; high: number; severe: number; no_travel: number };
  unknown_mi: number;
  avg_risk: number | null;
  max_level: number | null;
}

export interface WorstStretch {
  level: number;
  factors: string[];
  start_mile: number;
  end_mile: number;
  start_utc: string;
  end_utc: string;
  tz: string;
  place: string | null;
  peak: Partial<Record<"wind_mph" | "gust_mph" | "rain_in" | "snow_in", number>>;
}

export interface Route {
  id: string;
  flags: string[];
  notices: string[];
  source: string;
  distance_mi: number;
  drive_s: number;
  elapsed_s: number;
  arrival_utc: string;
  arrival_tz: string;
  geometry: [number, number][];
  /** [minutes since departure, lng, lat] */
  track: [number, number, number][];
  stops: Stop[];
  checkpoints: Checkpoint[];
  summary: RouteSummary;
  worst_stretch: WorstStretch | null;
  advisories: string[];
}

export interface Plan {
  plan_id: string;
  mode: "live" | "demo";
  generated_utc: string;
  forecast_source: string;
  inputs: {
    origin: Place;
    destination: Place;
    departure_utc: string;
    departure_tz: string;
    load_lb: number;
    gross_weight_lb: number;
    interval_mi: number;
    hos_enabled: boolean;
  };
  warnings: string[];
  routes: Route[];
  recommendation: {
    route_id: string;
    ranking: string[];
    status: "go" | "caution" | "no_travel";
    headline: string;
    reasons: { versus: string; criterion: string; text: string }[];
  };
  leave_later: null | {
    offset_h: number;
    departure_utc: string;
    route_id: string;
    criterion: string;
    text: string;
    summary: RouteSummary;
  };
}

export interface Heatmap {
  resolution: number;
  start_utc: string;
  hours: number;
  fields: string[];
  /** [h3 cell, overall, wind, rain, snow] — each a 49-char string of '0'..'4' or 'x' */
  cells: [string, string, string, string, string][];
  mode: string;
}

export interface PlanInput {
  origin: Place;
  destination: Place;
  departure_local: string | null;
  load_lb: number;
  interval_mi: 10 | 25 | 50;
  hos_enabled: boolean;
}

export type HeatLayer = "overall" | "wind" | "rain" | "snow";
