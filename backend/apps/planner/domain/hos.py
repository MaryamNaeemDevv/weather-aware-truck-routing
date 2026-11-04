"""Hours-of-Service (HOS) aware trip timeline.

Simplified FMCSA property-carrying rules, assuming the driver starts the trip
with a fresh clock:

* a 30-minute break is required after 8 cumulative hours of driving;
* a 10-hour off-duty rest is required after 11 hours of driving, which resets
  both counters.

The 14-hour on-duty window is not modelled separately: with no other on-duty
work, 11 h driving + 0.5 h break never exceeds it. This is documented in the
README as an assumption.

The timeline maps *drive time* (seconds behind the wheel) to *elapsed time*
(wall-clock seconds since departure). While stopped, the truck is parked, so
weather at a stop is not counted as road exposure.
"""

from __future__ import annotations

from dataclasses import dataclass

BREAK_AFTER_S = 8 * 3600
BREAK_S = 30 * 60
SHIFT_DRIVE_S = 11 * 3600
REST_S = 10 * 3600


@dataclass(frozen=True)
class Stop:
    kind: str  # "break" | "rest"
    at_drive_s: float  # drive time at which the stop begins
    duration_s: float


def plan_stops(total_drive_s: float, enabled: bool = True) -> list[Stop]:
    """Stops required to drive `total_drive_s`. No stop is scheduled at (or after) arrival."""
    if not enabled:
        return []
    stops: list[Stop] = []
    shift_start = 0.0
    while True:
        brk = shift_start + BREAK_AFTER_S
        if brk < total_drive_s:
            stops.append(Stop("break", brk, BREAK_S))
        rest = shift_start + SHIFT_DRIVE_S
        if rest < total_drive_s:
            stops.append(Stop("rest", rest, REST_S))
            shift_start = rest
        else:
            break
    return stops


def elapsed_at(drive_s: float, stops: list[Stop]) -> float:
    """Wall-clock seconds since departure when the truck has driven `drive_s`.

    A stop beginning exactly at `drive_s` has not started yet: this is the arrival
    time at that point, before parking.
    """
    return drive_s + sum(s.duration_s for s in stops if s.at_drive_s < drive_s)


def total_elapsed(total_drive_s: float, stops: list[Stop]) -> float:
    return total_drive_s + sum(s.duration_s for s in stops)


def drive_at_elapsed(elapsed_s: float, stops: list[Stop], total_drive_s: float) -> tuple[float, str | None]:
    """Inverse mapping for the map's 'truck ghost': drive time reached after `elapsed_s`
    on the clock, and the kind of stop the truck is in (if any)."""
    if elapsed_s <= 0:
        return 0.0, None
    clock = 0.0
    driven = 0.0
    for stop in stops:
        leg = stop.at_drive_s - driven
        if elapsed_s <= clock + leg:
            return driven + (elapsed_s - clock), None
        clock += leg
        driven = stop.at_drive_s
        if elapsed_s <= clock + stop.duration_s:
            return driven, stop.kind
        clock += stop.duration_s
    return min(total_drive_s, driven + (elapsed_s - clock)), None
