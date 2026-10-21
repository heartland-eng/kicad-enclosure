"""Parametric enclosure generator (build123d).

Turns parsed board geometry (``BoardData``) plus tunable ``EnclosureParams``
into two independent build123d solids: a base and a removable lid. Both are
returned as distinct parts (never fused) so the lid separates cleanly from the
base, per the acceptance criteria.

Geometry strategy (all in mm, board sits on standoffs above the floor):

  Z stack (base interior, measured from the floor bottom at Z=0):
    0 .. floor_thickness                      -> floor
    floor_thickness .. +standoff_height       -> standoffs (board seat)
    .. +board_thickness                       -> PCB
    .. +max_component_height                  -> components
    .. +clearance_z                           -> air gap below the lid

The cavity is the board outline offset outward by ``clearance_xy``; the outer
wall is that cavity offset outward again by ``wall_thickness``. The base is the
outer block minus the cavity block, plus standoffs, vents, cutouts and any
wall-mount feature. The lid is a top plate with a press-fit lip (inset tongue).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import build123d as b3d

from .data import BoardData, Component, EnclosureParams, MountingHole
from .geometry import ensure_ccw, nearest_edge, project_extent

# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------


@dataclass
class EnclosureResult:
    base: b3d.Part
    lid: b3d.Part
    wall_height: float        # total base height (floor bottom -> wall top)
    board_top_z: float        # Z of the top surface of the board
    cavity_outline: Optional[List[Tuple[float, float]]] = None  # informational

    def __post_init__(self):
        if self.cavity_outline is None:
            self.cavity_outline = []


# ---------------------------------------------------------------------------
# Small build helpers
# ---------------------------------------------------------------------------


def _extrude_face(points, amount: float) -> b3d.Solid:
    face = b3d.Polygon(*[(float(x), float(y)) for x, y in points]).face()
    return b3d.extrude(face, amount)


def _offset_face(points, amount: float) -> b3d.Face:
    face = b3d.Polygon(*[(float(x), float(y)) for x, y in points]).face()
    return b3d.offset(face, amount).face()


def _placed_box(center: Tuple[float, float, float],
                size: Tuple[float, float, float],
                tangent: Tuple[float, float],
                normal: Tuple[float, float]) -> b3d.Solid:
    """A Box whose local X aligns with `tangent`, local Z with +Z.

    The box is symmetric in its local Y axis, which ends up parallel to
    `normal` (sign irrelevant for a piercing cutout). `size` = (len_x, len_y,
    len_z) where len_x runs along the wall, len_y pierces the wall, len_z is
    vertical.
    """
    plane = b3d.Plane(origin=(center[0], center[1], center[2]),
                      x_dir=(tangent[0], tangent[1], 0.0),
                      z_dir=(0.0, 0.0, 1.0))
    return b3d.Box(size[0], size[1], size[2]).moved(plane)


def _horizontal_cylinder(center: Tuple[float, float, float], radius: float,
                         length: float, axis: Tuple[float, float]) -> b3d.Solid:
    """A cylinder whose axis lies in the XY plane along `axis`."""
    plane = b3d.Plane(origin=(center[0], center[1], center[2]),
                      z_dir=(axis[0], axis[1], 0.0))
    return b3d.Cylinder(radius, length).moved(plane)


# ---------------------------------------------------------------------------
# Feature builders
# ---------------------------------------------------------------------------


def _standoffs(base: b3d.Part, board: BoardData, p: EnclosureParams,
               floor_top: float) -> b3d.Part:
    part = base
    for mh in board.mounting_holes:
        boss_r = p.standoff_outer_d / 2.0
        pilot_r = p.standoff_inner_d / 2.0 + p.tolerance
        zc = floor_top + p.standoff_height / 2.0
        boss = b3d.Cylinder(boss_r, p.standoff_height).moved(
            b3d.Location((mh.x, mh.y, zc)))
        pilot = b3d.Cylinder(pilot_r, p.standoff_height + 0.2).moved(
            b3d.Location((mh.x, mh.y, zc)))
        part = part + (boss - pilot)
    return part


def _connector_cutouts(base: b3d.Part, board: BoardData, p: EnclosureParams,
                       outline, board_top: float) -> b3d.Part:
    part = base
    for c in board.components:
        if c.cutout_hint in ("", "none") or c.bbox is None:
            continue
        mnx, mny, mxx, mxy = c.bbox
        cx, cy = (mnx + mxx) / 2.0, (mny + mxy) / 2.0
        edge = nearest_edge(outline, cx, cy)
        if edge is None:
            continue
        t, n = edge["tangent"], edge["normal"]
        cp = edge["closest"]

        # Cutout width along the wall = component tangent extent + margin.
        width = project_extent(c.bbox, t) + 2.0 * p.cutout_clearance
        if width <= 0.0:
            continue
        # Thickness pierces the wall fully.
        thickness = p.wall_thickness + 2.0 * p.cutout_clearance + 0.4
        # Vertical span: board surface (minus margin) up over the component.
        z0 = board_top - p.cutout_clearance
        z1 = board_top + max(c.height, p.component_height) + p.cutout_clearance
        height = z1 - z0
        if height <= 0.0:
            continue

        cx_wall = cp[0] + n[0] * (p.clearance_xy + p.wall_thickness / 2.0)
        cy_wall = cp[1] + n[1] * (p.clearance_xy + p.wall_thickness / 2.0)
        cz = (z0 + z1) / 2.0

        cut = _placed_box((cx_wall, cy_wall, cz), (width, thickness, height), t, n)
        part = part - cut
    return part


def _cutout_edge_keys(board: BoardData, outline) -> set:
    """Return the set of edge endpoint tuples that host a connector cutout.

    A connector's bbox centre is snapped to its nearest outline edge; that
    edge must not also receive vent slots, otherwise a vent would merge into
    the connector opening.
    """
    keys = set()
    for c in board.components:
        if c.cutout_hint in ("", "none") or c.bbox is None:
            continue
        mnx, mny, mxx, mxy = c.bbox
        cx, cy = (mnx + mxx) / 2.0, (mny + mxy) / 2.0
        e = nearest_edge(outline, cx, cy)
        if e is not None:
            keys.add((tuple(e["p0"]), tuple(e["p1"])))
    return keys


def _vent_edges(board: BoardData, outline):
    """The two longest outline edges not carrying a connector cutout.

    These are the natural opposing vent walls. Falls back to simply the two
    longest edges when every long wall already has a cutout.
    """
    es = edges_list(outline)
    cutout_keys = _cutout_edge_keys(board, outline)

    def key(e):
        return (tuple(e[0]), tuple(e[1]))

    candidates = [e for e in es if key(e) not in cutout_keys]
    if not candidates:
        candidates = es
    candidates.sort(key=lambda e: math.dist(e[0], e[1]), reverse=True)
    return candidates[:2]


def _wall_vents(base: b3d.Part, board: BoardData, p: EnclosureParams,
                outline, floor_top: float, wall_height: float) -> b3d.Part:
    """Horizontal vent slots on two opposing (longest) walls."""
    part = base
    if p.vent_count <= 0 or p.vent_width <= 0.0:
        return part
    for p0, p1 in _vent_edges(board, outline):
        t, n = _edge_geometry(p0, p1)
        mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
        edge_len = math.dist(p0, p1)
        slot_len = min(p.vent_length, edge_len * 0.8)
        if slot_len <= 0.0:
            continue

        # Wall midline (radial centre of the wall band).
        wcx = mid[0] + n[0] * (p.clearance_xy + p.wall_thickness / 2.0)
        wcy = mid[1] + n[1] * (p.clearance_xy + p.wall_thickness / 2.0)

        # Column of slots centred vertically in the ventable band. Clamp the
        # requested count to what actually fits so slots never spill past the
        # wall top/bottom, and centre the column.
        lo, hi = floor_top + 2.0, wall_height - 2.0
        band = hi - lo
        if band <= p.vent_width:
            continue
        max_count = int((band - p.vent_width) // p.vent_spacing) + 1
        count = min(p.vent_count, max_count)
        total_h = (count - 1) * p.vent_spacing + p.vent_width
        z_start = lo + (band - total_h) / 2.0
        thickness = p.wall_thickness + 4.0
        for k in range(count):
            zc = z_start + k * p.vent_spacing + p.vent_width / 2.0
            slot = _placed_box((wcx, wcy, zc), (slot_len, thickness, p.vent_width), t, n)
            part = part - slot
    return part


def _lid_vents(lid: b3d.Part, board: BoardData, p: EnclosureParams,
               lid_top_z: float) -> b3d.Part:
    """Vent slots through the lid top plate (a single centred row)."""
    part = lid
    if p.vent_count <= 0 or p.vent_width <= 0.0:
        return part
    bb = part.bounding_box()
    cx = (bb.min.X + bb.max.X) / 2.0
    cy = (bb.min.Y + bb.max.Y) / 2.0
    total_w = (p.vent_count - 1) * p.vent_spacing + p.vent_length
    x0 = cx - total_w / 2.0 + p.vent_length / 2.0
    thickness = p.lid_thickness + 2.0
    zc = lid_top_z + p.lid_thickness / 2.0
    for k in range(p.vent_count):
        xc = x0 + k * p.vent_spacing
        slot = b3d.Box(p.vent_length, p.vent_width, thickness).moved(
            b3d.Location((xc, cy, zc)))
        part = part - slot
    return part


def _wall_mount(base: b3d.Part, board: BoardData, p: EnclosureParams,
                outline, floor_top: float, wall_height: float) -> b3d.Part:
    part = base
    style = p.mount_style
    if style in ("none", ""):
        return part

    # Back face = the outline edge whose midpoint has the lowest Y.
    back_edge = min(edges_list(outline), key=lambda e: (e[0][1] + e[1][1]) / 2.0)
    p0, p1 = back_edge
    t, n = _edge_geometry(p0, p1)
    edge_len = math.dist(p0, p1)
    zc = (floor_top + wall_height) / 2.0

    if style == "dual_tab":
        tab_w = 12.0
        tab_extend = 6.0
        tab_h = 10.0
        for frac in (0.2, 0.8):
            # point along the back edge
            bx = p0[0] + (p1[0] - p0[0]) * frac
            by = p0[1] + (p1[1] - p0[1]) * frac
            # protrude outward from the outer wall face
            cx = bx + n[0] * (p.clearance_xy + p.wall_thickness + tab_extend / 2.0)
            cy = by + n[1] * (p.clearance_xy + p.wall_thickness + tab_extend / 2.0)
            tab = _placed_box((cx, cy, zc), (tab_w, tab_extend, tab_h), t, n)
            hole = _horizontal_cylinder((cx, cy, zc), p.tab_hole_d / 2.0,
                                        tab_extend + 2.0, n)
            part = part + (tab - hole)
    elif style == "din_rail":
        # Simplified DIN-rail clip: a 35 mm wide recessed channel into the back
        # wall with a retaining lip along its lower edge. This is an
        # approximation of a TS35 snap-fit, not a certified DIN mount.
        rail_w = 35.0
        rail_h = 7.5
        mid = ((p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0)
        depth = 1.5
        # recessed channel cut into the outer face
        wcx = mid[0] + n[0] * (p.clearance_xy + p.wall_thickness - depth / 2.0)
        wcy = mid[1] + n[1] * (p.clearance_xy + p.wall_thickness - depth / 2.0)
        channel = _placed_box((wcx, wcy, zc),
                              (min(rail_w, edge_len * 0.9), depth + 0.4, rail_h), t, n)
        part = part - channel
        # retaining lip shelf just below the channel
        lip_z = zc - rail_h / 2.0 - 1.5
        lip = _placed_box((wcx, wcy + n[1] * 1.0, lip_z),
                          (min(rail_w, edge_len * 0.9), 3.0, 2.0), t, n)
        part = part + lip
    return part


def _edge_geometry(p0, p1):
    from .geometry import edge_tangent_normal
    return edge_tangent_normal(p0, p1)


def edges_list(points):
    from .geometry import edges as _edges
    return _edges(points)


def _fillet_z_edges(part: b3d.Part, radius: float) -> b3d.Part:
    if radius <= 0.0:
        return part
    try:
        z_edges = part.edges().filter_by(b3d.Axis.Z)
        if len(z_edges) == 0:
            return part
        return part.fillet(radius, z_edges)
    except Exception:
        # Filleting is cosmetic; never fail the build over it.
        return part


# ---------------------------------------------------------------------------
# Top-level generation
# ---------------------------------------------------------------------------


def _resolve_ceiling(board: BoardData, p: EnclosureParams) -> float:
    """Height of the tallest top-side component above the board surface."""
    tops = [c.height for c in board.components if c.side != "B.Cu"]
    if not tops:
        return p.component_height
    return max(tops)


def generate(board: BoardData, p: EnclosureParams) -> EnclosureResult:
    outline = ensure_ccw(board.outline)

    floor_top = p.floor_thickness
    board_bottom = floor_top + p.standoff_height
    board_top = board_bottom + board.thickness
    max_h = _resolve_ceiling(board, p)
    wall_height = board_top + max_h + p.clearance_z

    # Faces (outline -> cavity -> outer wall).
    cavity_face = _offset_face(outline, p.clearance_xy)
    outer_face = _offset_face(outline, p.clearance_xy + p.wall_thickness)

    # --- base ---------------------------------------------------------------
    outer_block = b3d.extrude(outer_face, wall_height)
    cavity_block = b3d.extrude(cavity_face, wall_height - floor_top).moved(
        b3d.Location((0.0, 0.0, floor_top)))
    base = outer_block - cavity_block

    base = _standoffs(base, board, p, floor_top)
    base = _connector_cutouts(base, board, p, outline, board_top)
    base = _wall_mount(base, board, p, outline, floor_top, wall_height)
    if p.vent_location in ("walls", "both"):
        base = _wall_vents(base, board, p, outline, floor_top, wall_height)
    base = _fillet_z_edges(base, p.fillet_radius)

    # --- lid ----------------------------------------------------------------
    # Generated in its ASSEMBLED position (sitting on top of the base walls),
    # so base and lid occupy disjoint volumes and can be shown together.
    lid_top = b3d.extrude(outer_face, p.lid_thickness).moved(
        b3d.Location((0.0, 0.0, wall_height)))
    # Lip = inset tongue that press-fits inside the base cavity.
    lip_face = b3d.offset(cavity_face, -(p.lid_gap)).face()
    lip = b3d.extrude(lip_face, p.lip_height).moved(
        b3d.Location((0.0, 0.0, wall_height - p.lip_height)))
    lid = lid_top + lip
    if p.vent_location in ("lid", "both"):
        lid = _lid_vents(lid, board, p, wall_height)
    lid = _fillet_z_edges(lid, p.fillet_radius)

    return EnclosureResult(
        base=base,
        lid=lid,
        wall_height=wall_height,
        board_top_z=board_top,
    )


def generate_from_dict(data: dict, params: Optional[dict] = None) -> EnclosureResult:
    from .data import board_from_dict, params_from_dict
    board = board_from_dict(data)
    p = params_from_dict(params if params is not None else data)
    return generate(board, p)
