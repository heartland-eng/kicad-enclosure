"""Typed data structures produced by the KiCad PCB parser.

These dataclasses mirror the ``enclosure-data-v1`` JSON contract defined in
SPEC.md (Section 3).  The parser emits :class:`EnclosureData`; calling
``to_dict()`` on it yields the JSON-ready structure that the enclosure
generator consumes.  A generator author may also feed a hand-authored JSON
document through ``EnclosureData.from_dict()`` to skip parsing entirely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

Point = Tuple[float, float]

__all__ = [
    "Point",
    "Polygon",
    "BBox",
    "Board",
    "MountingHole",
    "Component",
    "Source",
    "EnclosureData",
]

SCHEMA = "enclosure-data-v1"


@dataclass
class Polygon:
    points: List[Point] = field(default_factory=list)


@dataclass
class BBox:
    min: Point = (0.0, 0.0)
    max: Point = (0.0, 0.0)


@dataclass
class Board:
    thickness: float = 1.6
    outline: Polygon = field(default_factory=Polygon)
    holes: List[Polygon] = field(default_factory=list)


@dataclass
class MountingHole:
    x: float
    y: float
    diameter: float
    type: str  # "npth_pad" | "edge_circle"


@dataclass
class Component:
    ref: str
    value: str = ""
    side: str = "F.Cu"
    x: float = 0.0
    y: float = 0.0
    rot: float = 0.0
    bbox: Optional[BBox] = None
    courtyard: Optional[Polygon] = None
    height: float = 5.0
    cutout_hint: str = "none"
    on_edge: bool = False
    footprint: str = ""
    bbox_source: str = "none"  # courtyard | pads | silk | fab | default


@dataclass
class Source:
    path: str = ""
    generator: str = "kicad"
    kicad_version: Optional[int] = None


@dataclass
class EnclosureData:
    schema: str = SCHEMA
    units: str = "mm"
    source: Optional[Source] = None
    board: Optional[Board] = None
    mounting_holes: List[MountingHole] = field(default_factory=list)
    components: List[Component] = field(default_factory=list)
    config: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# JSON (de)serialization
# ---------------------------------------------------------------------------

def _pts_to_json(points: List[Point]) -> List[List[float]]:
    return [[round(x, 3), round(y, 3)] for x, y in points]


def _pts_from_json(pts: List[List[float]]) -> List[Point]:
    return [(float(x), float(y)) for x, y in pts]


def to_dict(data: EnclosureData) -> Dict[str, Any]:
    """Serialize an :class:`EnclosureData` to the ``enclosure-data-v1`` JSON shape."""
    out: Dict[str, Any] = {
        "$schema": data.schema,
        "units": data.units,
        "mounting_holes": [
            {"x": round(h.x, 3), "y": round(h.y, 3), "diameter": round(h.diameter, 3), "type": h.type}
            for h in data.mounting_holes
        ],
        "components": [],
        "config": data.config,
    }
    if data.source is not None:
        out["source"] = {
            "path": data.source.path,
            "generator": data.source.generator,
            "kicad_version": data.source.kicad_version,
        }
    if data.board is not None:
        b = data.board
        out["board"] = {
            "thickness": round(b.thickness, 3),
            "outline": {"points": _pts_to_json(b.outline.points)},
            "holes": [{"points": _pts_to_json(h.points)} for h in b.holes],
        }
    for c in data.components:
        item: Dict[str, Any] = {
            "ref": c.ref,
            "value": c.value,
            "side": c.side,
            "x": round(c.x, 3),
            "y": round(c.y, 3),
            "rot": round(c.rot, 3),
            "height": round(c.height, 3),
            "cutout_hint": c.cutout_hint,
            "on_edge": c.on_edge,
            "footprint": c.footprint,
            "bbox_source": c.bbox_source,
        }
        if c.bbox is not None:
            item["bbox"] = {"min": list(c.bbox.min), "max": list(c.bbox.max)}
        if c.courtyard is not None:
            item["courtyard"] = {"points": _pts_to_json(c.courtyard.points)}
        out["components"].append(item)
    return out


def from_dict(doc: Dict[str, Any]) -> EnclosureData:
    """Deserialize an ``enclosure-data-v1`` document (hand-authored JSON support)."""
    data = EnclosureData(schema=doc.get("$schema", SCHEMA), units=doc.get("units", "mm"))
    if "source" in doc:
        s = doc["source"]
        data.source = Source(
            path=s.get("path", ""),
            generator=s.get("generator", "kicad"),
            kicad_version=s.get("kicad_version"),
        )
    if "board" in doc:
        b = doc["board"]
        outline = Polygon(points=_pts_from_json(b["outline"]["points"]))
        holes = [Polygon(points=_pts_from_json(h["points"])) for h in b.get("holes", [])]
        data.board = Board(thickness=float(b.get("thickness", 1.6)), outline=outline, holes=holes)
    for h in doc.get("mounting_holes", []):
        data.mounting_holes.append(
            MountingHole(
                x=float(h["x"]), y=float(h["y"]),
                diameter=float(h["diameter"]), type=h.get("type", "npth_pad"),
            )
        )
    for c in doc.get("components", []):
        comp = Component(
            ref=c["ref"],
            value=c.get("value", ""),
            side=c.get("side", "F.Cu"),
            x=float(c.get("x", 0.0)),
            y=float(c.get("y", 0.0)),
            rot=float(c.get("rot", 0.0)),
            height=float(c.get("height", 5.0)),
            cutout_hint=c.get("cutout_hint", "none"),
            on_edge=bool(c.get("on_edge", False)),
            footprint=c.get("footprint", ""),
            bbox_source=c.get("bbox_source", "none"),
        )
        if "bbox" in c:
            comp.bbox = BBox(min=tuple(c["bbox"]["min"]), max=tuple(c["bbox"]["max"]))
        if "courtyard" in c:
            comp.courtyard = Polygon(points=_pts_from_json(c["courtyard"]["points"]))
        data.components.append(comp)
    data.config = dict(doc.get("config", {}))
    return data
