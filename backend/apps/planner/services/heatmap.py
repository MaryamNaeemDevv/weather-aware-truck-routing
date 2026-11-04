"""Corridor weather heatmap for the 0–48 hour slider.

The corridor is a band of H3 hexagons around all routes. For every cell we
precompute 49 hourly risk levels (now .. now+48h) — overall plus wind, rain and
snow separately — encoded as 49-character strings ('0'..'4', 'x' = no forecast).
The client downloads this once; moving the slider never touches the network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import h3
from django.conf import settings

from ..domain.geo import cumulative_miles
from ..domain.risk import assess
from ..domain.weather import floor_hour
from ..providers.registry import get_providers
from .weather_service import WeatherLookup

HOURS = 49
# (resolution, ring size) from finest to coarsest; the first one under the cap wins.
GRID_OPTIONS = [(5, 2), (5, 1), (4, 1), (4, 0), (3, 1), (3, 0)]


def _char(level) -> str:
    return "x" if level is None else str(int(level))


def _route_points(geometry: list[list[float]], step_mi: float) -> list[tuple[float, float]]:
    coords = [(lat, lng) for lng, lat in geometry]
    cum = cumulative_miles(coords)
    out, next_mark = [], 0.0
    for p, m in zip(coords, cum):
        if m >= next_mark:
            out.append(p)
            next_mark = m + step_mi
    out.append(coords[-1])
    return out


def corridor_cells(routes: dict[str, list[list[float]]], max_cells: int) -> tuple[int, set[str]]:
    for res, ring in GRID_OPTIONS:
        step = h3.average_hexagon_edge_length(res, unit="km") / 1.609344
        cells: set[str] = set()
        for geometry in routes.values():
            for lat, lng in _route_points(geometry, step):
                cells.update(h3.grid_disk(h3.latlng_to_cell(lat, lng, res), ring))
            if len(cells) > max_cells:
                break
        if len(cells) <= max_cells:
            return res, cells
    return res, cells  # coarsest option; may slightly exceed the cap on extreme trips


def build_heatmap(plan: dict) -> dict:
    providers = get_providers()
    res, cells = corridor_cells(plan["routes"], settings.PLANNER["HEATMAP_MAX_CELLS"])
    cells_sorted = sorted(cells)
    centers = {c: h3.cell_to_latlng(c) for c in cells_sorted}
    start = floor_hour(datetime.now(UTC))
    lookup = WeatherLookup(providers.weather, providers.mode, list(centers.values()), start + timedelta(hours=HOURS))
    load = plan["load_lb"]

    rows = []
    for cell in cells_sorted:
        series = lookup(*centers[cell])
        overall, wind, rain, snow = [], [], [], []
        for h in range(HOURS):
            wx = series.at(start + timedelta(hours=h)) if series else {}
            a = assess(wx.get("wind_mph"), wx.get("rain_in"), wx.get("snow_in"), load)
            overall.append(_char(a.level))
            wind.append(_char(a.wind_level))
            rain.append(_char(a.rain_level))
            snow.append(_char(a.snow_level))
        rows.append([cell, "".join(overall), "".join(wind), "".join(rain), "".join(snow)])

    return {
        "resolution": res,
        "start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hours": HOURS,
        "fields": ["h3", "overall", "wind", "rain", "snow"],
        "cells": rows,
        "mode": providers.mode,
    }
