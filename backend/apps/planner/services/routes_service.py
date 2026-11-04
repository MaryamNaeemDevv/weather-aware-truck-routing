"""Guarantee three genuinely different truck routes.

Routing APIs often return fewer alternatives than requested, or near-duplicates.
We keep provider routes that differ by at least 20% of their length, then fill
any gap by forcing routes through pass-through 'via' points offset to either
side of the fastest route.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

from ..domain import geo
from ..providers.base import LatLng, ProviderError, RawRoute, RoutingProvider

log = logging.getLogger(__name__)

TARGET_ROUTES = 3
MAX_OVERLAP = 0.8  # routes sharing more than this fraction are duplicates
MAX_VIA_SLOWDOWN = 2.0  # discard generated routes over 2x the fastest time
DETOUR_FLAG = 1.35  # label routes over 35% slower than the fastest

# (position along route, signed offset as a fraction of straight-line distance)
VIA_CANDIDATES = [(0.5, 0.15), (0.5, -0.15), (0.5, 0.28), (0.5, -0.28), (0.35, 0.2), (0.65, -0.2)]


@dataclass
class Candidate:
    raw: RawRoute
    line: geo.MeasuredLine
    flags: list[str] = field(default_factory=list)


def _distinct(new: geo.MeasuredLine, kept: list[Candidate]) -> bool:
    for c in kept:
        if max(geo.overlap_fraction(c.line, new), geo.overlap_fraction(new, c.line)) > MAX_OVERLAP:
            return False
    return True


def _via_point(line: geo.MeasuredLine, along: float, frac: float, straight_mi: float) -> LatLng:
    mile = line.length_mi * along
    p = line.point_at(mile)
    ahead = line.point_at(min(line.length_mi, mile + max(1.0, line.length_mi * 0.02)))
    behind = line.point_at(max(0.0, mile - max(1.0, line.length_mi * 0.02)))
    heading = geo.bearing_deg(behind, ahead)
    dist = min(max(abs(frac) * straight_mi, 2.0), 200.0)
    return geo.offset_point(p, heading + (90 if frac > 0 else -90), dist)


def candidate_routes(
    router: RoutingProvider,
    origin: LatLng,
    destination: LatLng,
    departure: datetime,
    gross_weight_lb: float,
) -> tuple[list[Candidate], list[str]]:
    warnings: list[str] = []
    raw = router.routes(origin, destination, departure, gross_weight_lb, alternatives=TARGET_ROUTES - 1)
    kept: list[Candidate] = []
    for r in raw:
        line = geo.measure(r.coords, r.vertex_drive_s)
        if _distinct(line, kept):
            kept.append(Candidate(r, line))
    kept.sort(key=lambda c: c.line.drive_s)

    if len(kept) < TARGET_ROUTES:
        fastest = kept[0]
        straight = geo.haversine_mi(origin, destination)
        vias = [_via_point(fastest.line, along, frac, straight) for along, frac in VIA_CANDIDATES]
        # Ask in pairs (both sides of the road) to keep API usage low.
        for i in range(0, len(vias), 2):
            if len(kept) >= TARGET_ROUTES:
                break
            batch = vias[i : i + 2]
            with ThreadPoolExecutor(max_workers=len(batch)) as pool:
                results = list(
                    pool.map(lambda v: _try_via(router, origin, destination, departure, gross_weight_lb, v), batch)
                )
            for r in results:
                if r is None or len(kept) >= TARGET_ROUTES:
                    continue
                line = geo.measure(r.coords, r.vertex_drive_s)
                if line.drive_s > fastest.line.drive_s * MAX_VIA_SLOWDOWN:
                    continue
                if _distinct(line, kept):
                    r.source = "via"
                    kept.append(Candidate(r, line))

    if len(kept) < TARGET_ROUTES:
        warnings.append(
            f"Only {len(kept)} distinct truck route{'s' if len(kept) != 1 else ''} exist between these places."
        )

    kept.sort(key=lambda c: c.line.drive_s)
    best = kept[0].line.drive_s
    for c in kept:
        if best > 0 and c.line.drive_s > best * DETOUR_FLAG:
            c.flags.append("detour")
    return kept, warnings


def _try_via(router, origin, destination, departure, gross, via) -> RawRoute | None:
    try:
        routes = router.routes(origin, destination, departure, gross, alternatives=0, via=via)
    except ProviderError as exc:
        log.info("via candidate %s rejected: %s", via, exc.message)
        return None
    return routes[0] if routes else None
