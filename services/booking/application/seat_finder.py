"""find_seats business logic (AI agent requirements doc §2.1, design.md
Appendix A's `POST /showtimes/{id}/find-seats`) -- ranks groups of seats
for a showtime against a customer's stated preferences. Pure functions
over the seat rows `PostgresSeatRepository.get_all_for_showtime` already
fetched; no DB access of its own, same repository/application-logic
split `BookingOrchestrator` already uses.

Coordinate normalization (confirmed with user, not literally stated in
the requirements doc): `position_x`/`position_y` are raw layout units
written by the admin canvas's placement tools (e.g. a grid's rows land
at integer y = 0, 1, 2, ...; see apps/admin-web/src/lib/placementTools.ts
and SeatCanvas.tsx's PIXELS_PER_UNIT) -- not 0-1 normalized. The
requirements doc's zone thresholds (front <0.33, middle 0.33-0.66, back
>0.66) and centrality scoring (proximity to x=0.5) only make sense
against 0-1 coordinates, so both axes are min-max normalized against
this showtime's own seat layout bounds (every materialized seat,
regardless of status, so the bounds don't shift as seats get booked)
before classification.

Adjacency (also confirmed with user): two seats are adjacent only if
they share the same row (exact `position_y` match -- every placement
tool lays out one row at one constant y) and have consecutive
`position_x` values within ADJACENCY_X_TOLERANCE. Grouping purely by
zone, which can span several rows, would otherwise pair seats from
different rows that happen to share a similar x but aren't physically
side-by-side.
"""
import re
from typing import Optional

ZONE_FRONT_MAX = 0.33
ZONE_MIDDLE_MAX = 0.66
# Generous versus the 1-unit row/column spacing every placement tool in
# apps/admin-web/src/lib/placementTools.ts uses by default -- tolerates
# slightly uneven spacing (e.g. a hand-placed single seat) without
# treating a real gap (an aisle) as adjacent.
ADJACENCY_X_TOLERANCE = 1.5
CENTRALITY_TARGET = 0.5
MAX_GROUPS_RETURNED = 3

_ZONE_DISPLAY = {"front": "front", "middle": "centre", "back": "back"}
_TRAILING_DIGITS_RE = re.compile(r"^(.*?)(\d+)$")


def find_seat_groups(all_seats: list[dict], count: int, preferences: dict) -> list[dict]:
    """`all_seats`: every SHOWTIME_SEAT row for this showtime (any
    status), each with position_x/position_y/seat_type/label/price/id
    and the DB-computed `is_effectively_available` flag. Returns up to
    MAX_GROUPS_RETURNED ranked, non-overlapping seat groups of exactly
    `count` seats."""
    if count < 1 or not all_seats:
        return []

    adjacent = preferences.get("adjacent", True)
    pref_zone = preferences.get("zone") or "any"
    pref_seat_type = preferences.get("seat_type") or "any"

    norm_y = _normalize([s["position_y"] for s in all_seats])
    norm_x = _normalize([s["position_x"] for s in all_seats])

    candidates = []
    for s in all_seats:
        if not s["is_effectively_available"]:
            continue
        enriched = dict(s)
        enriched["_zone"] = _zone_for(norm_y[s["position_y"]])
        enriched["_norm_x"] = norm_x[s["position_x"]]
        candidates.append(enriched)

    if pref_seat_type != "any":
        candidates = [s for s in candidates if s["seat_type"].lower() == pref_seat_type.lower()]
    if pref_zone != "any":
        candidates = [s for s in candidates if s["_zone"] == pref_zone]

    groups = _find_adjacent_groups(candidates, count) if adjacent else _find_loose_groups(candidates, count)
    ranked = sorted(groups, key=lambda g: _group_sort_key(g, pref_zone))
    distinct = _select_non_overlapping(ranked, MAX_GROUPS_RETURNED)
    return [_build_response_group(g) for g in distinct]


def _zone_for(normalized_y: float) -> str:
    if normalized_y < ZONE_FRONT_MAX:
        return "front"
    if normalized_y <= ZONE_MIDDLE_MAX:
        return "middle"
    return "back"


def _normalize(values: list[float]) -> dict[float, float]:
    """Maps each raw value to its 0-1 position within [min, max] of the
    full showtime's layout. A single distinct value (e.g. a one-row
    screen) maps everything to 0.0 -- a real but uncommon edge case."""
    lo, hi = min(values), max(values)
    span = hi - lo
    if span == 0:
        return {v: 0.0 for v in values}
    return {v: (v - lo) / span for v in values}


def _find_adjacent_groups(seats: list[dict], count: int) -> list[list[dict]]:
    by_row: dict[float, list[dict]] = {}
    for s in seats:
        by_row.setdefault(s["position_y"], []).append(s)

    groups: list[list[dict]] = []
    for row_seats in by_row.values():
        row_seats.sort(key=lambda s: s["position_x"])
        run = [row_seats[0]]
        for seat in row_seats[1:]:
            if seat["position_x"] - run[-1]["position_x"] <= ADJACENCY_X_TOLERANCE:
                run.append(seat)
            else:
                groups.extend(_windows(run, count))
                run = [seat]
        groups.extend(_windows(run, count))
    return groups


def _windows(run: list[dict], count: int) -> list[list[dict]]:
    if len(run) < count:
        return []
    return [run[i : i + count] for i in range(len(run) - count + 1)]


def _find_loose_groups(seats: list[dict], count: int) -> list[list[dict]]:
    """adjacent=false: any `count` seats matching the filters, grouped
    without a contiguity requirement. Sliding windows over seats sorted
    by centrality so the "best" loose groupings still surface first."""
    ordered = sorted(seats, key=lambda s: abs(s["_norm_x"] - CENTRALITY_TARGET))
    return _windows(ordered, count)


def _group_sort_key(group: list[dict], pref_zone: str) -> tuple[int, float]:
    zone = group[0]["_zone"]
    zone_priority = 0 if (pref_zone == "any" or zone == pref_zone) else 1
    centrality = sum(abs(s["_norm_x"] - CENTRALITY_TARGET) for s in group) / len(group)
    return (zone_priority, centrality)


def _select_non_overlapping(ranked_groups: list[list[dict]], max_groups: int) -> list[list[dict]]:
    """Greedily keep the best-ranked groups, skipping any that share a
    seat with one already selected -- avoids presenting the customer
    near-duplicate options (e.g. F4-5 and F5-6) as if they were 3
    distinct choices."""
    selected: list[list[dict]] = []
    used_seat_ids: set = set()
    for group in ranked_groups:
        group_ids = {s["id"] for s in group}
        if group_ids & used_seat_ids:
            continue
        selected.append(group)
        used_seat_ids |= group_ids
        if len(selected) >= max_groups:
            break
    return selected


def _split_label(label: str) -> tuple[str, Optional[str]]:
    """'F4' -> ('F', '4'); a label with no trailing digits (unusual but
    possible under the freeform model, §4.5) returns (label, None)."""
    m = _TRAILING_DIGITS_RE.match(label)
    if m:
        return m.group(1), m.group(2)
    return label, None


def _build_response_group(group: list[dict]) -> dict:
    seats_sorted = sorted(group, key=lambda s: s["position_x"])
    total_price = sum(float(s["price"]) for s in seats_sorted)
    zone = seats_sorted[0]["_zone"]
    row_label, first_num = _split_label(seats_sorted[0]["label"])
    _, last_num = _split_label(seats_sorted[-1]["label"])

    if first_num is not None and last_num is not None:
        seat_range = f"{first_num}-{last_num}" if first_num != last_num else first_num
    else:
        seat_range = f"{seats_sorted[0]['label']}-{seats_sorted[-1]['label']}"

    seat_type_display = seats_sorted[0]["seat_type"].lower()
    description = f"Row {row_label}, seats {seat_range}, {_ZONE_DISPLAY[zone]}, {seat_type_display}"

    return {
        "seats": [
            {"id": str(s["id"]), "label": s["label"], "seat_type": s["seat_type"], "price": float(s["price"])}
            for s in seats_sorted
        ],
        "description": description,
        "total_price": total_price,
        "zone": zone,
    }
