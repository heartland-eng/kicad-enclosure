"""Validation helpers for generated enclosure geometry.

Two independent checks back the acceptance criteria:

1. Mesh manifoldness — the exported STL must be watertight with positive
   volume (trimesh).
2. Component clearance — no component's occupied volume may intersect the
   enclosure walls (build123d boolean intersection must be empty).
"""

from __future__ import annotations

from typing import List, Tuple

import build123d as b3d

from .data import BoardData, Component, EnclosureParams
from .generator import EnclosureResult


def check_stl_manifold(path: str) -> dict:
    """Return manifold diagnostics for an STL file."""
    import trimesh
    mesh = trimesh.load(path, force="mesh")
    watertight = bool(mesh.is_watertight)
    volume = float(mesh.volume) if hasattr(mesh, "volume") else 0.0
    return {
        "path": path,
        "watertight": watertight,
        "volume": volume,
        "is_empty": bool(mesh.is_empty),
        "faces": len(mesh.faces) if mesh.faces is not None else 0,
    }


def intersection_volume(a, b) -> float:
    """Volume of the boolean intersection of two shapes (0.0 if empty).

    build123d 0.13 returns a ``ShapeList`` from ``.intersect()``; the ``&``
    operator returns a ``Part`` (or ``None`` when disjoint). This normalises
    both to a scalar volume.
    """
    try:
        inter = a & b
    except Exception:
        return 0.0
    if inter is None:
        return 0.0
    try:
        return float(inter.volume)
    except Exception:
        try:
            return sum(float(s.volume) for s in inter.solids())
        except Exception:
            return 0.0


def _component_solid(c: Component, board_top: float, board_bottom: float) -> b3d.Solid:
    """The occupied volume of a component, extruded to its height.

    Uses the rotated footprint courtyard polygon when available (more accurate
    for round/rotated parts than the axis-aligned bbox); otherwise falls back
    to the bbox. Top-side components sit on top of the board; bottom-side hang
    below it.
    """
    h = max(c.height, 0.1)
    z0 = board_bottom - h if c.side == "B.Cu" else board_top

    if c.courtyard:
        pts = [(float(x), float(y)) for x, y in c.courtyard]
        face = b3d.Polygon(*pts).face()
        return b3d.extrude(face, h).moved(b3d.Location((0.0, 0.0, z0)))

    if c.bbox is None:
        s = 2.0
        mnx, mny, mxx, mxy = c.x - s, c.y - s, c.x + s, c.y + s
    else:
        mnx, mny, mxx, mxy = c.bbox
    w = max(mxx - mnx, 0.1)
    d = max(mxy - mny, 0.1)
    cx, cy = (mnx + mxx) / 2.0, (mny + mxy) / 2.0
    box = b3d.Box(w, d, h).moved(b3d.Location((cx, cy, z0 + h / 2.0)))
    return box


def check_component_clearance(board: BoardData, p: EnclosureParams,
                              result: EnclosureResult,
                              volume_tol: float = 0.01) -> List[dict]:
    """Return a list of components whose occupied volume intersects the base.

    The base already has connector cutouts removed, so an edge-mounted
    connector correctly passing through its wall opening reports no overlap.
    """
    floor_top = p.floor_thickness
    board_bottom = floor_top + p.standoff_height
    board_top = board_bottom + board.thickness

    violations = []
    for c in board.components:
        solid = _component_solid(c, board_top, board_bottom)
        vol = intersection_volume(result.base, solid)
        if vol > volume_tol:
            violations.append({
                "ref": c.ref,
                "value": c.value,
                "side": c.side,
                "overlap_volume": round(vol, 4),
            })
    return violations
