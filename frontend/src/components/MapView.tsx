import { cellToBoundary } from "h3-js";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useMemo, useRef, useState } from "react";
import { fmtLocal, fmtRate, level, LEVELS, UNKNOWN } from "../lib/format";
import { truckAt } from "../lib/track";
import { useUi } from "../store";
import type { Heatmap, Plan } from "../types";

// Bundlers relocate the library, so point MapLibre at its bundled worker explicitly.
maplibregl.setWorkerUrl(workerUrl);

const STYLE_URL = "https://tiles.openfreemap.org/styles/positron";
const FALLBACK_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "bg", type: "background", paint: { "background-color": "#E6EAED" } }],
};
const ROUTE_BLUE = "#1D4F91";
const ROUTE_SLATE = "#7D8A95";
const LAYER_PREFIX = { overall: "o", wind: "w", rain: "r", snow: "s" } as const;
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };

async function loadStyle(): Promise<maplibregl.StyleSpecification | string> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 4000);
    const res = await fetch(STYLE_URL, { signal: ctrl.signal });
    clearTimeout(timer);
    if (!res.ok) throw new Error(String(res.status));
    return (await res.json()) as maplibregl.StyleSpecification;
  } catch {
    return FALLBACK_STYLE; // keep the app usable if the basemap host is down
  }
}

const levelColor: maplibregl.ExpressionSpecification = [
  "match",
  ["get", "level"],
  ...LEVELS.flatMap((l) => [l.code, l.color]),
  UNKNOWN.color,
] as unknown as maplibregl.ExpressionSpecification;

function heatColor(key: string): maplibregl.ExpressionSpecification {
  return [
    "match",
    ["get", key],
    ...LEVELS.flatMap((l) => [l.code, l.color]),
    -1,
    UNKNOWN.color,
    "rgba(0,0,0,0)",
  ] as unknown as maplibregl.ExpressionSpecification;
}

function heatOpacity(key: string): maplibregl.ExpressionSpecification {
  return ["match", ["get", key], 0, 0.1, 1, 0.34, 2, 0.46, 3, 0.56, 4, 0.66, -1, 0.14, 0] as maplibregl.ExpressionSpecification;
}

function heatFeatures(hm: Heatmap): GeoJSON.FeatureCollection {
  const decode = (ch: string) => (ch === "x" ? -1 : ch.charCodeAt(0) - 48);
  return {
    type: "FeatureCollection",
    features: hm.cells.map(([cell, o, w, r, s]) => {
      const props: Record<string, number | string> = { cell };
      for (let h = 0; h < hm.hours; h++) {
        props[`o${h}`] = decode(o[h]);
        props[`w${h}`] = decode(w[h]);
        props[`r${h}`] = decode(r[h]);
        props[`s${h}`] = decode(s[h]);
      }
      const ring = cellToBoundary(cell, true);
      return { type: "Feature", properties: props, geometry: { type: "Polygon", coordinates: [[...ring, ring[0]]] } };
    }),
  };
}

function popupHtml(plan: Plan, routeId: string, i: number) {
  const route = plan.routes.find((r) => r.id === routeId);
  const cp = route?.checkpoints[i];
  if (!route || !cp) return "";
  const lv = level(cp.level);
  const why = cp.drivers.length ? ` from ${cp.drivers.join(" + ")}` : "";
  const esc = cp.load_escalated ? `<div class="pop-note">Raised by the load rule (${plan.inputs.load_lb.toLocaleString()} lb)</div>` : "";
  return `<div class="pop">
    <div class="pop-head"><span class="swatch" style="background:${lv.color}"></span><b>${lv.label}</b>${why}</div>
    <div class="pop-sub">Route ${route.id}, mile ${cp.mile.toFixed(0)}, ${fmtLocal(cp.eta_utc, cp.tz)}</div>
    <table class="pop-wx">
      <tr><td>Wind</td><td>${fmtRate(cp.wx.wind_mph, "mph", 0)}${cp.wx.gust_mph != null ? ` (gusts ${cp.wx.gust_mph.toFixed(0)})` : ""}</td></tr>
      <tr><td>Rain</td><td>${fmtRate(cp.wx.rain_in, "in/hr")}</td></tr>
      <tr><td>Snow</td><td>${fmtRate(cp.wx.snow_in, "in/hr")}</td></tr>
      <tr><td>Temp</td><td>${cp.wx.temp_f != null ? `${cp.wx.temp_f.toFixed(0)}°F` : "—"}</td></tr>
    </table>${esc}</div>`;
}

interface Props {
  plan: Plan | null;
  heatmap: Heatmap | null;
}

export function MapView({ plan, heatmap }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const ghosts = useRef<Map<string, maplibregl.Marker>>(new Map());
  const popup = useRef<maplibregl.Popup | null>(null);
  const planRef = useRef<Plan | null>(null);
  const [ready, setReady] = useState(false);

  const { selectedRoute, hour, layer, showHeatmap, select, hoveredCheckpoint } = useUi();
  const selected = selectedRoute ?? plan?.recommendation.route_id ?? null;
  planRef.current = plan;

  // --- Map init (once) ---------------------------------------------------------
  useEffect(() => {
    let cancelled = false;
    loadStyle().then((style) => {
      if (cancelled || !container.current) return;
      const map = new maplibregl.Map({
        container: container.current,
        style,
        center: [-97, 39],
        zoom: 3.6,
        attributionControl: { compact: true },
      });
      map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      map.addControl(new maplibregl.ScaleControl({ unit: "imperial" }), "bottom-right");
      map.on("load", () => {
        map.addSource("heat", { type: "geojson", data: EMPTY });
        map.addSource("routes", { type: "geojson", data: EMPTY });
        map.addSource("checkpoints", { type: "geojson", data: EMPTY });
        map.addSource("stops", { type: "geojson", data: EMPTY });
        map.addLayer({
          id: "heat",
          type: "fill",
          source: "heat",
          paint: { "fill-color": heatColor("o0"), "fill-opacity": heatOpacity("o0"), "fill-outline-color": "rgba(255,255,255,0.25)" },
        });
        map.addLayer({
          id: "route-casing",
          type: "line",
          source: "routes",
          layout: { "line-cap": "round", "line-join": "round" },
          paint: { "line-color": "#ffffff", "line-width": ["case", ["get", "selected"], 9, 6] },
        });
        map.addLayer({
          id: "route-line",
          type: "line",
          source: "routes",
          layout: { "line-cap": "round", "line-join": "round", "line-sort-key": ["case", ["get", "selected"], 1, 0] },
          paint: {
            "line-color": ["case", ["get", "selected"], ROUTE_BLUE, ROUTE_SLATE],
            "line-width": ["case", ["get", "selected"], 5, 3],
            "line-opacity": ["case", ["get", "selected"], 1, 0.75],
          },
        });
        map.addLayer({
          id: "checkpoints",
          type: "circle",
          source: "checkpoints",
          paint: {
            "circle-color": levelColor,
            "circle-radius": [
              "interpolate",
              ["linear"],
              ["zoom"],
              4,
              ["case", ["get", "selected"], 4, 2.5],
              9,
              ["case", ["get", "selected"], 7, 4],
            ],
            "circle-opacity": ["case", ["get", "selected"], 1, 0.55],
            "circle-stroke-color": "#ffffff",
            "circle-stroke-width": ["case", ["get", "selected"], 1.5, 0.75],
          },
        });
        map.addLayer({
          id: "stops",
          type: "circle",
          source: "stops",
          paint: {
            "circle-radius": ["case", ["==", ["get", "kind"], "rest"], 6, 4.5],
            "circle-color": "#ffffff",
            "circle-stroke-color": "#24313B",
            "circle-stroke-width": ["case", ["==", ["get", "kind"], "rest"], 3, 2],
          },
        });

        popup.current = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 10, maxWidth: "280px" });
        map.on("mousemove", "checkpoints", (e) => {
          const f = e.features?.[0];
          const p = planRef.current;
          if (!f || !p) return;
          map.getCanvas().style.cursor = "pointer";
          const { route, i } = f.properties as { route: string; i: number };
          popup.current!.setLngLat(e.lngLat).setHTML(popupHtml(p, route, i)).addTo(map);
        });
        map.on("mouseleave", "checkpoints", () => {
          map.getCanvas().style.cursor = "";
          popup.current?.remove();
        });
        map.on("click", "route-line", (e) => {
          const id = e.features?.[0]?.properties?.id as string | undefined;
          if (id) useUi.getState().select(id);
        });
        map.on("mouseenter", "route-line", () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", "route-line", () => (map.getCanvas().style.cursor = ""));
        setReady(true);
      });
      mapRef.current = map;
      if (import.meta.env.DEV) (window as unknown as { __map: maplibregl.Map }).__map = map;
    });
    return () => {
      cancelled = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  // --- Plan geometry -----------------------------------------------------------
  const routeData = useMemo<GeoJSON.FeatureCollection>(() => {
    if (!plan) return EMPTY;
    return {
      type: "FeatureCollection",
      features: plan.routes.map((r) => ({
        type: "Feature",
        properties: { id: r.id, selected: r.id === selected },
        geometry: { type: "LineString", coordinates: r.geometry },
      })),
    };
  }, [plan, selected]);

  const checkpointData = useMemo<GeoJSON.FeatureCollection>(() => {
    if (!plan) return EMPTY;
    return {
      type: "FeatureCollection",
      features: plan.routes
        .flatMap((r) => r.checkpoints.map((c) => ({ r, c })))
        .sort((a, b) => Number(a.r.id === selected) - Number(b.r.id === selected))
        .map(({ r, c }) => ({
          type: "Feature",
          properties: { route: r.id, i: c.i, level: c.level, selected: r.id === selected },
          geometry: { type: "Point", coordinates: [c.lng, c.lat] },
        })),
    };
  }, [plan, selected]);

  const stopData = useMemo<GeoJSON.FeatureCollection>(() => {
    const r = plan?.routes.find((x) => x.id === selected);
    if (!r) return EMPTY;
    return {
      type: "FeatureCollection",
      features: r.stops.map((s) => ({
        type: "Feature",
        properties: { kind: s.kind },
        geometry: { type: "Point", coordinates: [s.lng, s.lat] },
      })),
    };
  }, [plan, selected]);

  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    (map.getSource("routes") as maplibregl.GeoJSONSource).setData(routeData);
    (map.getSource("checkpoints") as maplibregl.GeoJSONSource).setData(checkpointData);
    (map.getSource("stops") as maplibregl.GeoJSONSource).setData(stopData);
  }, [ready, routeData, checkpointData, stopData]);

  // Fit to the routes once per new plan.
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !plan) return;
    const b = new maplibregl.LngLatBounds();
    plan.routes.forEach((r) => r.geometry.forEach((c) => b.extend(c)));
    const wide = window.innerWidth > 900;
    map.fitBounds(b, { padding: wide ? { top: 60, bottom: 130, left: 60, right: 60 } : 40, duration: 700 });
    popup.current?.remove();
  }, [ready, plan]);

  // --- Heatmap -----------------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    (map.getSource("heat") as maplibregl.GeoJSONSource).setData(heatmap ? heatFeatures(heatmap) : EMPTY);
  }, [ready, heatmap]);

  // The slider only swaps which pre-loaded property the GPU reads — no data upload.
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const key = `${LAYER_PREFIX[layer]}${hour}`;
    map.setPaintProperty("heat", "fill-color", heatColor(key));
    map.setPaintProperty("heat", "fill-opacity", heatOpacity(key));
    map.setLayoutProperty("heat", "visibility", showHeatmap ? "visible" : "none");
  }, [ready, hour, layer, showHeatmap]);

  // --- Truck ghosts --------------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map) return;
    const markers = ghosts.current;
    if (!plan || !heatmap) {
      markers.forEach((m) => m.remove());
      markers.clear();
      return;
    }
    const depMs = Date.parse(plan.inputs.departure_utc);
    const atMs = Date.parse(heatmap.start_utc) + hour * 3600_000;
    const minutes = (atMs - depMs) / 60000;
    const live = new Set<string>();
    for (const r of plan.routes) {
      live.add(r.id);
      const pos = truckAt(r, minutes, depMs);
      let m = markers.get(r.id);
      if (!m) {
        const el = document.createElement("div");
        el.className = "ghost";
        el.textContent = r.id;
        m = new maplibregl.Marker({ element: el }).setLngLat([pos.lng, pos.lat]).addTo(map);
        markers.set(r.id, m);
      }
      m.setLngLat([pos.lng, pos.lat]);
      const el = m.getElement();
      el.dataset.selected = String(r.id === selected);
      el.dataset.state = pos.state;
      el.title = `Route ${r.id}: ${
        { before: "not yet departed", driving: "driving", break: "30-min break", rest: "10-h rest", arrived: "arrived" }[
          pos.state
        ]
      }`;
    }
    markers.forEach((m, id) => {
      if (!live.has(id)) {
        m.remove();
        markers.delete(id);
      }
    });
  }, [ready, plan, heatmap, hour, selected]);

  // Fly to a checkpoint hovered in the panel.
  useEffect(() => {
    const map = mapRef.current;
    if (!ready || !map || !plan || !hoveredCheckpoint) return;
    const r = plan.routes.find((x) => x.id === hoveredCheckpoint.route);
    const cp = r?.checkpoints[hoveredCheckpoint.i];
    if (!cp) return;
    popup.current?.setLngLat([cp.lng, cp.lat]).setHTML(popupHtml(plan, r!.id, cp.i)).addTo(map);
  }, [ready, plan, hoveredCheckpoint]);

  useEffect(() => {
    if (plan && !plan.routes.some((r) => r.id === selectedRoute)) select(null);
  }, [plan, selectedRoute, select]);

  return <div ref={container} className="map" aria-label="Map of route options and weather" role="region" />;
}
