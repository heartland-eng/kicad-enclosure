"""Pure-Python 2-D polygon helpers used by the enclosure generator.

These operate on the board outline (a closed ring of (x, y) mm points) to
decide wall normals, tangents, and connector cutout placement. They are
deliberately free of build123d so the geometry reasoning is testable in
isolation.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

XY = Tuple[float, float]


def polygon_area(points: List[XY]) -> float:
    """Signed shoelace area. Positive == counter-clockwise."""
    n = len(points)
    if n < 3:
        return 0.0
    acc = 0.0
    for i in range(n):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % n]
        acc += x0 * y1 - x1 * y0
    return acc / 2.0


def ensure_ccw(points: List[XY]) -> List[XY]:
    """Return the ring in counter-clockwise winding order."""
    if polygon_area(points) >= 0.0:
        return list(points)
    return list(reversed(points))


def edges(points: List[XY]) -> List[Tuple[XY, XY]]:
    n = len(points)
    return [(points[i], points[(i + 1) % n]) for i in range(n)]


def _unit(v: XY) -> XY:
    x, y = v
    m = math.hypot(x, y)
    if m == 0.0:
        return (0.0, 0.0)
    return (x / m, y / m)


def edge_tangent_normal(p0: XY, p1: XY) -> Tuple[XY, XY]:
    """Return (tangent_unit, outward_normal_unit) for a CCW polygon edge.

    For a CCW ring the interior lies to the left of each directed edge, so the
    outward normal is the tangent rotated -90 degrees (clockwise).
    """
    t = _unit((p1[0] - p0[0], p1[1] - p0[1]))
    n = (t[1], -t[0])
    return t, n


def point_segment_distance(px: float, py: float, p0: XY, p1: XY) -> Tuple[float, XY]:
    """Distance from point to segment, plus the closest point on the segment."""
    vx, vy = p1[0] - p0[0], p1[1] - p0[1]
    wx, wy = px - p0[0], py - p0[1]
    l2 = vx * vx + vy * vy
    if l2 == 0.0:
        return math.hypot(px - p0[0], py - p0[1]), p0
    tt = (wx * vx + wy * vy) / l2
    tt = max(0.0, min(1.0, tt))
    cx, cy = p0[0] + tt * vx, p0[1] + tt * vy
    return math.hypot(px - cx, py - cy), (cx, cy)


def nearest_edge(points: List[XY], px: float, py: float):
    """Find the outline edge nearest a point.

    Returns a dict with the edge endpoints, tangent, outward normal, the
    closest point on the edge, and the distance to it.
    """
    best = None
    for p0, p1 in edges(points):
        d, cp = point_segment_distance(px, py, p0, p1)
        if best is None or d < best["dist"]:
            t, n = edge_tangent_normal(p0, p1)
            best = {"p0": p0, "p1": p1, "tangent": t, "normal": n,
                    "closest": cp, "dist": d}
    return best


def project_extent(bbox: Tuple[float, float, float, float], axis: XY) -> float:
    """Project the four bbox corners onto a unit axis; return the span."""
    mnx, mny, mxx, mxy = bbox
    corners = [(mnx, mny), (mnx, mxy), (mxx, mny), (mxx, mxy)]
    vals = [x * axis[0] + y * axis[1] for x, y in corners]
    return max(vals) - min(vals)


def longest_edges(points: List[XY], k: int = 2) -> List[Tuple[XY, XY]]:
    """Return the k longest outline edges (for vent placement)."""
    es = sorted(edges(points), key=lambda e: math.dist(e[0], e[1]), reverse=True)
    return es[:k]


def side_edges(points: List[XY]):
    """Return the two outline edges whose outward normal is most horizontal.

    These are the left and right 'side' walls. Vents go here so they never
    collide with the wall-mount feature on the back (most-negative-Y) face.
    Each entry is (p0, p1, tangent, outward_normal, |normal.x|).
    """
    scored = []
    for p0, p1 in edges(points):
        t, n = edge_tangent_normal(p0, p1)
        scored.append(((p0, p1), t, n, abs(n[0])))
    scored.sort(key=lambda item: -item[3])
    return scored[:2]
