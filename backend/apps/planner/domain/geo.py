"""Geometry helpers: distances, polyline measurement and checkpoint sampling.

Coordinates are (lat, lng) tuples in degrees. Distances are miles.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass

EARTH_RADIUS_MI = 3958.7613
KM_PER_MI = 1.609344

LatLng = tuple[float, float]


def haversine_mi(a: LatLng, b: LatLng) -> float:
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(min(1.0, math.sqrt(h)))


def cumulative_miles(coords: Sequence[LatLng]) -> list[float]:
    out = [0.0]
    for prev, cur in zip(coords, coords[1:]):
        out.append(out[-1] + haversine_mi(prev, cur))
    return out


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass(frozen=True)
class MeasuredLine:
    """A polyline with cumulative distance (and optionally drive time) per vertex."""

    coords: list[LatLng]
    cum_mi: list[float]
    cum_drive_s: list[float]

    @property
    def length_mi(self) -> float:
        return self.cum_mi[-1]

    @property
    def drive_s(self) -> float:
        return self.cum_drive_s[-1]

    def _locate(self, mile: float) -> tuple[int, float]:
        """Return (segment index i, fraction t) so the point lies between vertex i and i+1."""
        mile = min(max(mile, 0.0), self.length_mi)
        i = bisect.bisect_right(self.cum_mi, mile) - 1
        i = min(max(i, 0), len(self.coords) - 2)
        seg = self.cum_mi[i + 1] - self.cum_mi[i]
        t = 0.0 if seg <= 0 else (mile - self.cum_mi[i]) / seg
        return i, min(max(t, 0.0), 1.0)

    def point_at(self, mile: float) -> LatLng:
        i, t = self._locate(mile)
        (a_lat, a_lng), (b_lat, b_lng) = self.coords[i], self.coords[i + 1]
        return (lerp(a_lat, b_lat, t), lerp(a_lng, b_lng, t))

    def drive_s_at(self, mile: float) -> float:
        """Drive time from the start to this mile, interpolated within the segment,
        so slow and fast stretches keep their own speeds."""
        i, t = self._locate(mile)
        return lerp(self.cum_drive_s[i], self.cum_drive_s[i + 1], t)

    def mile_at_drive_s(self, drive_s: float) -> float:
        """Inverse of drive_s_at — where the truck is after `drive_s` seconds of driving."""
        drive_s = min(max(drive_s, 0.0), self.drive_s)
        i = bisect.bisect_right(self.cum_drive_s, drive_s) - 1
        i = min(max(i, 0), len(self.coords) - 2)
        seg = self.cum_drive_s[i + 1] - self.cum_drive_s[i]
        t = 0.0 if seg <= 0 else (drive_s - self.cum_drive_s[i]) / seg
        return lerp(self.cum_mi[i], self.cum_mi[i + 1], min(max(t, 0.0), 1.0))


def measure(
    coords: Sequence[LatLng], vertex_drive_s: Sequence[float] | None = None, total_drive_s: float | None = None
) -> MeasuredLine:
    """Build a MeasuredLine.

    `vertex_drive_s` — cumulative drive seconds at each vertex (from the routing
    provider's per-span durations). If absent, `total_drive_s` is spread
    proportionally to distance as a fallback.
    """
    coords = [tuple(c) for c in coords]  # type: ignore[misc]
    if len(coords) < 2:
        raise ValueError("A route needs at least two coordinates")
    cum = cumulative_miles(coords)
    if vertex_drive_s is not None:
        if len(vertex_drive_s) != len(coords):
            raise ValueError("vertex_drive_s must align with coords")
        drive = [float(x) for x in vertex_drive_s]
        # Guard against non-monotonic provider data.
        for k in range(1, len(drive)):
            drive[k] = max(drive[k], drive[k - 1])
    else:
        total = float(total_drive_s or 0.0)
        length = cum[-1] or 1.0
        drive = [total * (m / length) for m in cum]
    return MeasuredLine(list(coords), cum, drive)


def sample_miles(length_mi: float, interval_mi: float) -> list[float]:
    """Checkpoint positions every `interval_mi`, always including origin and destination.

    A final stub shorter than 1% of the interval is merged into the destination so we
    never emit two checkpoints at (almost) the same place.
    """
    if interval_mi <= 0:
        raise ValueError("interval must be positive")
    if length_mi <= 0:
        return [0.0]
    marks: list[float] = []
    k = 0
    while k * interval_mi < length_mi:
        marks.append(k * interval_mi)
        k += 1
    if length_mi - marks[-1] < 0.01 * interval_mi and len(marks) > 1:
        marks[-1] = length_mi
    else:
        marks.append(length_mi)
    return marks


def owned_miles(marks: Sequence[float], length_mi: float) -> list[float]:
    """Midpoint attribution: each checkpoint owns half the distance to each neighbour.

    The result sums exactly to the route length, so 'Severe miles' is auditable.
    """
    if len(marks) == 1:
        return [length_mi]
    out = []
    for i, m in enumerate(marks):
        lo = 0.0 if i == 0 else (marks[i - 1] + m) / 2
        hi = length_mi if i == len(marks) - 1 else (m + marks[i + 1]) / 2
        out.append(hi - lo)
    return out


def bearing_deg(a: LatLng, b: LatLng) -> float:
    lat1, lat2 = math.radians(a[0]), math.radians(b[0])
    dlng = math.radians(b[1] - a[1])
    x = math.sin(dlng) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlng)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def offset_point(origin: LatLng, bearing: float, distance_mi: float) -> LatLng:
    """Destination point given start, bearing (deg) and distance along a great circle."""
    lat1, lng1 = map(math.radians, origin)
    brg = math.radians(bearing)
    d = distance_mi / EARTH_RADIUS_MI
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(brg))
    lng2 = lng1 + math.atan2(
        math.sin(brg) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return (math.degrees(lat2), (math.degrees(lng2) + 540) % 360 - 180)


def overlap_fraction(a: MeasuredLine, b: MeasuredLine, within_mi: float = 0.6, step_mi: float = 2.0) -> float:
    """Share of route `b` (sampled every `step_mi`) lying within `within_mi` of route `a`.

    Uses a local equirectangular projection — accurate enough at the scale of a
    road corridor and dependency-free.
    """
    from shapely.geometry import LineString, Point

    lat0 = math.radians((a.coords[0][0] + a.coords[-1][0]) / 2)
    kx = math.cos(lat0) * 69.172
    ky = 69.0

    def proj(p: LatLng) -> tuple[float, float]:
        return (p[1] * kx, p[0] * ky)

    line_a = LineString([proj(p) for p in a.coords])
    samples = sample_miles(b.length_mi, step_mi)
    near = sum(1 for m in samples if line_a.distance(Point(proj(b.point_at(m)))) <= within_mi)
    return near / len(samples)
