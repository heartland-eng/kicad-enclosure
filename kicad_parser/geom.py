"""2D geometry helpers for the KiCad PCB parser (standard library only).

All quantities are in millimetres, matching KiCad's implicit unit for the
``.kicad_pcb`` format.  Angles are in degrees (KiCad stores footprint and pad
rotation in degrees; only *text* angles are tenths-of-a-degree).
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

__all__ = [
    "dist",
    "rotate",
    "transform",
    "signed_area",
    "area",
    "centroid",
    "is_ccw",
    "ensure_ccw",
    "ensure_cw",
    "bbox_of",
    "convex_hull",
    "circle_through",
    "arc_points",
    "circle_points",
    "point_in_polygon",
    "segments_intersect",
]


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def rotate(p: Point, angle_deg: float) -> Point:
    """Rotate a point counter-clockwise about the origin by ``angle_deg``."""
    r = math.radians(angle_deg)
    x, y = p
    return (x * math.cos(r) - y * math.sin(r), x * math.sin(r) + y * math.cos(r))


def transform(p: Point, origin: Point, angle_deg: float, flip_y: bool = False) -> Point:
    """Map a footprint-local point to absolute board coordinates.

    The KiCad placement is ``(at X Y angle)``: a local point is rotated by
    ``angle`` then translated by ``(X, Y)``.  Bottom-side (``B.Cu``) footprints
    are mirrored across the X axis, which we approximate as a Y flip (see the
    SPEC note in README; it only affects the sign of local Y and, for an
    axis-aligned bounding box, rarely changes the result).
    """
    q = rotate(p, angle_deg)
    if flip_y:
        q = (q[0], -q[1])
    return (origin[0] + q[0], origin[1] + q[1])


def signed_area(points: Sequence[Point]) -> float:
    """Shoelace signed area; positive == counter-clockwise."""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def area(points: Sequence[Point]) -> float:
    return abs(signed_area(points))


def centroid(points: Sequence[Point]) -> Point:
    if not points:
        return (0.0, 0.0)
    a = signed_area(points)
    if abs(a) < 1e-12:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cx = cy = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    cx /= 6.0 * a
    cy /= 6.0 * a
    return (cx, cy)


def is_ccw(points: Sequence[Point]) -> bool:
    return signed_area(points) > 0


def ensure_ccw(points: Sequence[Point]) -> List[Point]:
    pts = list(points)
    return pts if is_ccw(pts) else list(reversed(pts))


def ensure_cw(points: Sequence[Point]) -> List[Point]:
    pts = list(points)
    return list(reversed(pts)) if is_ccw(pts) else pts


def bbox_of(points: Iterable[Point]) -> Tuple[Point, Point]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys)), (max(xs), max(ys))


def convex_hull(points: Iterable[Point]) -> List[Point]:
    """Andrew's monotone chain; returns hull vertices in CCW order."""
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts

    def cross(o: Point, a: Point, b: Point) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: List[Point] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 1e-9:
            lower.pop()
        lower.append(p)
    upper: List[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 1e-9:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def circle_through(a: Point, b: Point, c: Point) -> Optional[Tuple[Point, float]]:
    """Circle passing through three points -> ((cx, cy), radius), or None if collinear."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = (
        (ax * ax + ay * ay) * (by - cy)
        + (bx * bx + by * by) * (cy - ay)
        + (cx * cx + cy * cy) * (ay - by)
    ) / d
    uy = (
        (ax * ax + ay * ay) * (cx - bx)
        + (bx * bx + by * by) * (ax - cx)
        + (cx * cx + cy * cy) * (bx - ax)
    ) / d
    center = (ux, uy)
    return center, dist(center, a)


def arc_points(start: Point, mid: Point, end: Point, n: int = 32) -> List[Point]:
    """Discretize a KiCad ``gr_arc`` (start -> mid -> end) into ``n`` points."""
    c = circle_through(start, mid, end)
    if c is None:
        return [start, end]
    center, r = c

    def ang(p: Point) -> float:
        return math.atan2(p[1] - center[1], p[0] - center[0])

    a0, a1, a2 = ang(start), ang(mid), ang(end)
    sweep = (a2 - a0) % (2.0 * math.pi)
    # KiCad arcs are CCW start->mid->end; detect a CW sweep and correct it.
    if (a1 - a0) % (2.0 * math.pi) > sweep and sweep > 0:
        sweep -= 2.0 * math.pi
    pts: List[Point] = [start]
    for i in range(1, n):
        a = a0 + sweep * i / n
        pts.append((center[0] + r * math.cos(a), center[1] + r * math.sin(a)))
    pts.append(end)
    return pts


def circle_points(center: Point, end: Point, n: int = 64) -> List[Point]:
    """Discretize a KiCad ``gr_circle`` into ``n`` points (closed polygon)."""
    r = dist(center, end)
    return [
        (center[0] + r * math.cos(2.0 * math.pi * i / n),
         center[1] + r * math.sin(2.0 * math.pi * i / n))
        for i in range(n)
    ]


def arc_points_by_angle(start: Point, end: Point, angle_deg: float, n: int = 32) -> List[Point]:
    """Discretize a legacy (KiCad 5) arc defined by ``start``, ``end`` and a signed
    sweep ``angle_deg`` (positive == counter-clockwise).  The centre is reconstructed
    from the chord length and sweep angle."""
    if abs(angle_deg) < 1e-9:
        return [start, end]
    theta = math.radians(angle_deg)
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-9:
        return [start, end]
    radius = chord / (2.0 * math.sin(abs(theta) / 2.0))
    # Unit vector left of the chord direction start -> end.
    px = -dy / chord
    py = dx / chord
    h = radius * math.cos(abs(theta) / 2.0)
    mx = (start[0] + end[0]) / 2.0
    my = (start[1] + end[1]) / 2.0
    if theta > 0:  # CCW: centre lies to the left of the chord
        cx, cy = mx + px * h, my + py * h
    else:          # CW: centre lies to the right
        cx, cy = mx - px * h, my - py * h
    a0 = math.atan2(start[1] - cy, start[0] - cx)
    pts = [start]
    for i in range(1, n):
        a = a0 + theta * i / n
        pts.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    pts.append(end)
    return pts


def point_in_polygon(p: Point, poly: Sequence[Point]) -> bool:
    """Ray-casting point-in-polygon; boundary points count as inside (approx)."""
    x, y = p
    n = len(poly)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def segments_intersect(a: Point, b: Point, c: Point, d: Point, tol: float = 1e-6) -> bool:
    """True if segments a-b and c-d intersect (including collinear overlap)."""

    def ccw(p: Point, q: Point, r: Point) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    d1 = ccw(c, d, a)
    d2 = ccw(c, d, b)
    d3 = ccw(a, b, c)
    d4 = ccw(a, b, d)
    if ((d1 > tol and d2 < -tol) or (d1 < -tol and d2 > tol)) and (
        (d3 > tol and d4 < -tol) or (d3 < -tol and d4 > tol)
    ):
        return True

    def on_seg(p: Point, q: Point, r: Point) -> bool:
        return (
            min(p[0], r[0]) - tol <= q[0] <= max(p[0], r[0]) + tol
            and min(p[1], r[1]) - tol <= q[1] <= max(p[1], r[1]) + tol
        )

    if abs(d1) <= tol and on_seg(c, d, a):
        return True
    if abs(d2) <= tol and on_seg(c, d, b):
        return True
    if abs(d3) <= tol and on_seg(a, b, c):
        return True
    if abs(d4) <= tol and on_seg(a, b, d):
        return True
    return False
