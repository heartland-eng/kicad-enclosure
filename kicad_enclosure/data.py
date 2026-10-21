"""Data model for the kicad-enclosure generator.

Mirrors the ``enclosure-data-v1`` intermediate JSON contract defined in SPEC.md
(parent research task t_8dab9867). The generator consumes ``BoardData`` +
``EnclosureParams`` and emits two build123d solids (base and lid).

All units are millimetres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

# A 2-D point as (x, y) in millimetres.
XY = Tuple[float, float]
# An axis-aligned bounding box as (min_x, min_y, max_x, max_y).
BBox = Tuple[float, float, float, float]


@dataclass
class MountingHole:
    """A board mounting hole in absolute board coordinates."""

    x: float
    y: float
    diameter: float
    type: str = "npth_pad"  # npth_pad | edge_circle


@dataclass
class Component:
    """A PCB component: position, footprint bbox, height, and cutout hint."""

    ref: str
    value: str = ""
    side: str = "F.Cu"  # F.Cu (top) | B.Cu (bottom)
    x: float = 0.0
    y: float = 0.0
    rot: float = 0.0  # degrees
    bbox: Optional[BBox] = None  # absolute-space AABB (min_x, min_y, max_x, max_y)
    courtyard: Optional[List[XY]] = None  # absolute rotated footprint corners
    height: float = 5.0  # component height above board surface (KiCad has no field)
    cutout_hint: str = "none"
    on_edge: bool = False

    @property
    def bbox_center(self) -> XY:
        if self.bbox is None:
            return (self.x, self.y)
        mnx, mny, mxx, mxy = self.bbox
        return ((mnx + mxx) / 2.0, (mny + mxy) / 2.0)


@dataclass
class BoardData:
    """Parsed board geometry as consumed by the generator."""

    outline: List[XY]  # closed outer boundary ring, absolute coords (mm)
    holes: List[List[XY]] = field(default_factory=list)  # interior cutouts
    mounting_holes: List[MountingHole] = field(default_factory=list)
    components: List[Component] = field(default_factory=list)
    thickness: float = 1.6  # board thickness


@dataclass
class EnclosureParams:
    """All tunable dimensions for the enclosure. Every value is a named,
    commented variable so the whole design is parametric."""

    # --- walls / clearance ---------------------------------------------------
    wall_thickness: float = 2.0      # shell thickness of the walls
    clearance_xy: float = 0.4        # board-to-wall breathing room (X/Y)
    clearance_z: float = 2.0         # air gap above the tallest component
    floor_thickness: float = 2.0     # base floor thickness
    lid_thickness: float = 2.0       # lid top-plate thickness

    # --- standoffs (board mounting) -----------------------------------------
    standoff_height: float = 5.0     # board sits this far above the floor
    standoff_outer_d: float = 6.0    # boss diameter
    standoff_inner_d: float = 3.0    # pilot bore (3 mm self-tapping screw)

    # --- lid fit -------------------------------------------------------------
    lid_gap: float = 0.2             # clearance between lip and base walls
    lip_height: float = 2.0          # tongue depth that press-fits into base

    # --- finish / tolerance --------------------------------------------------
    fillet_radius: float = 0.5       # outer vertical edge round
    tolerance: float = 0.2           # added to mating/interference faces only

    # --- components ----------------------------------------------------------
    component_height: float = 5.0    # default height when a component has none

    # --- vents ---------------------------------------------------------------
    vent_count: int = 5              # slots per wall / lid row
    vent_width: float = 3.0          # slot opening (short dimension)
    vent_length: float = 20.0        # slot length (long dimension)
    vent_spacing: float = 6.0        # pitch between slots
    vent_location: str = "walls"     # walls | lid | both

    # --- wall mounting -------------------------------------------------------
    mount_style: str = "none"        # none | dual_tab | din_rail
    tab_hole_d: float = 4.0          # wall-mount tab screw hole

    # --- connector cutouts ---------------------------------------------------
    cutout_clearance: float = 0.5    # extra margin around connector openings


# ---------------------------------------------------------------------------
# JSON <-> dataclass normalisation (tolerant of missing keys)
# ---------------------------------------------------------------------------

def _as_xy_list(items) -> List[XY]:
    return [(float(p[0]), float(p[1])) for p in items]


def board_from_dict(d: dict) -> BoardData:
    # The enclosure-data-v1 schema keeps the board's own geometry under
    # `board`, but mounting holes and components are TOP-LEVEL siblings
    # (see SPEC.md section 3). Read each field with a top-level-first,
    # board-nested fallback so both layouts work.
    board = d.get("board", {}) if isinstance(d.get("board"), dict) else {}

    def pick(name):
        if name in d and d[name] is not None:
            return d[name]
        if name in board and board[name] is not None:
            return board[name]
        return None

    outline_raw = board.get("outline", d.get("outline"))
    if isinstance(outline_raw, dict):
        outline_raw = outline_raw.get("points", [])
    outline = _as_xy_list(outline_raw or [])

    holes_raw = board.get("holes", [])
    holes = [
        _as_xy_list(h.get("points", h))
        for h in holes_raw
    ]
    mounting_holes = [
        MountingHole(
            x=float(m["x"]),
            y=float(m["y"]),
            diameter=float(m.get("diameter", 3.2)),
            type=m.get("type", "npth_pad"),
        )
        for m in (pick("mounting_holes") or [])
    ]
    components = []
    for c in (pick("components") or []):
        bbox = None
        if c.get("bbox") is not None:
            bb = c["bbox"]
            bbox = (float(bb["min"][0]), float(bb["min"][1]),
                    float(bb["max"][0]), float(bb["max"][1]))
        courtyard = None
        if c.get("courtyard") is not None:
            courtyard = _as_xy_list(c["courtyard"].get("points", c["courtyard"]))
        components.append(Component(
            ref=str(c.get("ref", "?")),
            value=str(c.get("value", "")),
            side=str(c.get("side", "F.Cu")),
            x=float(c.get("x", 0.0)),
            y=float(c.get("y", 0.0)),
            rot=float(c.get("rot", 0.0)),
            bbox=bbox,
            courtyard=courtyard,
            height=float(c.get("height", 5.0)),
            cutout_hint=str(c.get("cutout_hint", "none")),
            on_edge=bool(c.get("on_edge", False)),
        ))
    return BoardData(
        outline=outline,
        holes=holes,
        mounting_holes=mounting_holes,
        components=components,
        thickness=float(board.get("thickness", d.get("thickness", 1.6))),
    )


def params_from_dict(d: dict) -> EnclosureParams:
    cfg = d.get("config", d)
    aliases = {
        "clearance": "clearance_z",  # task body calls Z clearance "clearance"
    }
    known = {f.name for f in EnclosureParams.__dataclass_fields__.values()}

    kwargs = {}
    for key, value in cfg.items():
        key = aliases.get(key, key)
        if key in known and value is not None:
            kwargs[key] = value

    # mount_style aliases
    if "mount_style" in kwargs:
        ms = str(kwargs["mount_style"]).lower().replace("-", "_")
        kwargs["mount_style"] = ms

    return EnclosureParams(**kwargs)
