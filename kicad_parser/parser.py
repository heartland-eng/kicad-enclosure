"""KiCad ``.kicad_pcb`` -> :class:`EnclosureData` parser.

Pure standard library, no KiCad installation required.  See SPEC.md for the
format reference and the README for usage.

Extraction pipeline
-------------------
1. **Board outline** — collect every graphic primitive on the ``Edge.Cuts``
   layer (``gr_line``/``gr_arc``/``gr_circle``/``gr_rect``/``gr_poly``),
   discretize arcs and circles into polylines, stitch the segments by shared
   endpoints into closed loops, and pick the largest-area loop as the outer
   boundary (normalized CCW) with the rest as interior holes.
2. **Mounting holes** — two encodings, both detected: ``np_thru_hole`` pads
   (position transformed from footprint-local to absolute) and ``Edge.Cuts``
   ``gr_circle`` items that are *not* the outer outline.
3. **Component bounding boxes** — from the footprint courtyard (``F.CrtYd`` /
   ``B.CrtYd``), rotated/translated to absolute space; falls back to pad
   extents + silk + fab graphics, then to a configurable default square.
4. **Connector / cutout hints** — reference-designator/value/footprint-name
   matching against configurable patterns plus ``on_edge`` detection (bbox
   touching or crossing the board outline).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import geom
from .sexpr import Symbol, loads
from .model import (
    BBox,
    Board,
    Component,
    EnclosureData,
    MountingHole,
    Polygon,
    Source,
)

__all__ = ["ParserConfig", "ParseError", "parse", "parse_file", "parse_bytes"]

Point = Tuple[float, float]

EDGE_CUTS = "Edge.Cuts"

# Default connector/cutout patterns.  Ordered most-specific first (so that
# "usb_c" is not swallowed by the bare "usb" pattern).
DEFAULT_HINT_PATTERNS: List[Tuple[str, Tuple[str, ...]]] = [
    ("usb_micro", ("usb_micro", "micro_usb", "micro-usb", "microusb", "usb micro")),
    ("usb_c", ("usb_c", "usb-c", "usbc", "type_c", "type-c", "usb type c", "usb3_c")),
    ("usb_a", ("usb_a", "usb-a", "usba", "usb a", "usb")),
    ("rj45", ("rj45", "rj-45", "ethernet", "8p8c", "magjack")),
    ("hdmi", ("hdmi",)),
    ("display", ("display", "oled", "lcd", "tft", "touchscreen", "eink", "e-ink")),
    ("sd", ("micro_sd", "microsd", "micro-sd", "sd_card", "sdcard", "sd card")),
    ("barrel", ("barrel", "dc_jack", "dc-jack", "dcjack", "power_jack", "powerjack")),
]


class ParseError(ValueError):
    """Raised when the PCB cannot be parsed into a usable geometry (CLI exit 2)."""


@dataclass
class ParserConfig:
    """Tunables for extraction.  All are optional with sensible defaults."""

    component_height: float = 5.0          # default Z height of a component (KiCad has none)
    board_thickness_default: float = 1.6   # used when (general (thickness ...)) is absent
    arc_segments: int = 32                 # discretization of gr_arc
    circle_segments: int = 64              # discretization of gr_circle
    edge_tolerance: float = 0.1            # mm; on_edge detection margin
    stitch_tolerance: float = 1e-4         # mm; endpoint snapping for loop stitching
    default_bbox_size: float = 5.0         # mm; fallback square side when no geometry exists
    cutout_overrides: Dict[str, str] = field(default_factory=dict)  # ref -> cutout_hint
    connector_patterns: List[Tuple[str, Tuple[str, ...]]] = field(
        default_factory=lambda: list(DEFAULT_HINT_PATTERNS)
    )
    fallback_warnings: bool = True         # warn (stderr) when a bbox falls back


# ---------------------------------------------------------------------------
# s-expression navigation helpers
# ---------------------------------------------------------------------------

def _child(node: Sequence, name: str) -> Optional[Sequence]:
    for c in node[1:]:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def _children(node: Sequence, name: str) -> List[Sequence]:
    return [c for c in node[1:] if isinstance(c, list) and c and c[0] == name]


def _f(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _xy(node: Sequence, *names: str) -> Optional[Point]:
    """Read an ``(X Y)`` or ``(name X Y)`` coordinate pair, skipping rotation."""
    if len(names) == 0 and len(node) >= 3 and isinstance(node[1], (str, Symbol)) and isinstance(node[2], (str, Symbol)):
        x = _f(node[1])
        y = _f(node[2])
        if x is not None and y is not None:
            return (x, y)
    for name in names:
        c = _child(node, name)
        if c is not None and len(c) >= 3:
            x = _f(c[1])
            y = _f(c[2])
            if x is not None and y is not None:
                return (x, y)
    return None


def _arc_from_node(node: Sequence, n: int = 32) -> List[Point]:
    """Discretize a ``gr_arc`` / ``fp_arc`` node, supporting both encodings:

    * KiCad 6+: ``(start X Y) (mid X Y) (end X Y)`` (three-point arc);
    * KiCad 5:  ``(start X Y) (end X Y) (angle DEG)`` (signed sweep angle).

    Returns a list of points (degenerate arcs collapse to a two-point line).
    """
    s = _xy(node, "start")
    e = _xy(node, "end")
    if s is None or e is None:
        return []
    m = _xy(node, "mid")
    if m is not None:
        return geom.arc_points(s, m, e, n)
    a = _child(node, "angle")
    if a is not None and len(a) >= 2:
        ang = _f(a[1])
        if ang is not None:
            return geom.arc_points_by_angle(s, e, ang, n)
    return [s, e]


# ---------------------------------------------------------------------------
# Board outline: Edge.Cuts collection + stitching
# ---------------------------------------------------------------------------

def _collect_edge_segments(
    nodes: List[Sequence],
    config: ParserConfig,
) -> Tuple[List[Tuple[Point, Point, str]], List[Tuple[int, Point, Point]]]:
    """Return (segments, circles).

    ``segments`` is a list of ``(p1, p2, source_tag)`` where ``source_tag`` is
    ``"line"``, ``"arc"``, ``"rect"``, ``"poly"`` or ``"circle:<i>"``.  ``circles``
    is a list of ``(index, center, end)`` for every ``gr_circle`` on Edge.Cuts
    (kept so we can distinguish a round board outline from a circular hole).
    """
    segments: List[Tuple[Point, Point, str]] = []
    circles: List[Tuple[int, Point, Point]] = []
    circle_idx = 0

    for node in nodes:
        head = node[0]
        if head not in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly"):
            continue
        layer = ""
        lc = _child(node, "layer")
        if lc is not None and len(lc) >= 2 and isinstance(lc[1], str):
            layer = lc[1]
        if layer != EDGE_CUTS:
            continue

        if head == "gr_line":
            s = _xy(node, "start")
            e = _xy(node, "end")
            if s and e:
                segments.append((s, e, "line"))

        elif head == "gr_arc":
            pts = _arc_from_node(node, config.arc_segments)
            for a, b in zip(pts[:-1], pts[1:]):
                segments.append((a, b, "arc"))

        elif head == "gr_circle":
            c = _xy(node, "center")
            e = _xy(node, "end")
            if c and e:
                circles.append((circle_idx, c, e))
                pts = geom.circle_points(c, e, config.circle_segments)
                tag = f"circle:{circle_idx}"
                for a, b in zip(pts, pts[1:]):
                    segments.append((a, b, tag))
                segments.append((pts[-1], pts[0], tag))
                circle_idx += 1

        elif head == "gr_rect":
            s = _xy(node, "start")
            e = _xy(node, "end")
            if s and e:
                (x0, y0), (x1, y1) = s, e
                corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
                for a, b in zip(corners, corners[1:] + corners[:1]):
                    segments.append((a, b, "rect"))

        elif head == "gr_poly":
            pts_node = _child(node, "pts")
            if pts_node is None:
                continue
            # (pts (xy X Y) (xy X Y) ...)
            pts = []
            for xy_node in pts_node[1:]:
                if isinstance(xy_node, list) and xy_node and xy_node[0] == "xy" and len(xy_node) >= 3:
                    x = _f(xy_node[1])
                    y = _f(xy_node[2])
                    if x is not None and y is not None:
                        pts.append((x, y))
            if len(pts) >= 3:
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    segments.append((a, b, "poly"))

    return segments, circles


def _stitch_segments(
    segments: List[Tuple[Point, Point, str]],
    tol: float,
) -> List[List[Point]]:
    """Join segments by shared endpoints into closed loops (point lists, unclosed)."""
    from collections import defaultdict

    def snap(p: Point) -> Tuple[int, int]:
        return (round(p[0] / tol), round(p[1] / tol))

    node_edges: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for i, (p1, p2, _src) in enumerate(segments):
        node_edges[snap(p1)].append(i)
        node_edges[snap(p2)].append(i)

    used = [False] * len(segments)
    loops: List[List[Point]] = []

    for start_i in range(len(segments)):
        if used[start_i]:
            continue
        p1, p2, _ = segments[start_i]
        points = [p1]
        cur_pt = p1
        cur_edge = start_i
        guard = len(segments) + 1
        while guard > 0:
            guard -= 1
            used[cur_edge] = True
            e1, e2, _ = segments[cur_edge]
            nxt = e2 if snap(e1) == snap(cur_pt) else e1
            cur_pt = nxt
            points.append(cur_pt)
            if snap(cur_pt) == snap(points[0]):
                break
            cands = [e for e in node_edges[snap(cur_pt)] if e != cur_edge and not used[e]]
            if not cands:
                cands = [e for e in node_edges[snap(cur_pt)] if e != cur_edge]
            if not cands:
                break
            cur_edge = cands[0]

        if len(points) >= 3 and snap(points[0]) == snap(points[-1]):
            loops.append(points[:-1])

    return loops


def _extract_outline(
    nodes: List[Sequence],
    config: ParserConfig,
) -> Tuple[Polygon, List[Polygon], List[Tuple[int, Point, Point]]]:
    segments, circles = _collect_edge_segments(nodes, config)
    loops = _stitch_segments(segments, config.stitch_tolerance)
    if not loops:
        raise ParseError("no closed Edge.Cuts outline could be stitched")

    # Largest unsigned area == outer boundary.
    loops.sort(key=lambda lp: geom.area(lp), reverse=True)
    outer = geom.ensure_ccw(loops[0])
    holes = [geom.ensure_cw(lp) for lp in loops[1:]]
    return Polygon(points=outer), [Polygon(points=h) for h in holes], circles


# ---------------------------------------------------------------------------
# Mounting holes
# ---------------------------------------------------------------------------

def _mounting_holes(
    footprints: List[Sequence],
    outline: Polygon,
    edge_circles: List[Tuple[int, Point, Point]],
    config: ParserConfig,
) -> List[MountingHole]:
    holes: List[MountingHole] = []

    # 1) NPTH pads (absolute position via footprint transform).
    for fp in footprints:
        at = _fp_at(fp)
        if at is None:
            continue
        fx, fy, fangle, flip = at
        for pad in _children(fp, "pad"):
            ptype = pad[2] if len(pad) > 2 else ""
            if ptype != "np_thru_hole":
                continue
            pat = _xy(pad, "at")
            if pat is None:
                pat = (0.0, 0.0)
            abs_pt = geom.transform(pat, (fx, fy), fangle, flip_y=flip)
            d = _pad_diameter(pad)
            if d is not None and d > 0:
                holes.append(MountingHole(x=abs_pt[0], y=abs_pt[1], diameter=d, type="npth_pad"))

    # 2) Edge.Cuts circles that are not the outer boundary.
    outer_area = geom.area(outline.points)
    for _idx, center, end in edge_circles:
        r = geom.dist(center, end)
        # A round board (the circle *is* the outline) is not a mounting hole.
        if abs(geom.area(geom.circle_points(center, end, config.circle_segments)) - outer_area) < outer_area * 1e-3:
            continue
        holes.append(
            MountingHole(x=center[0], y=center[1], diameter=2.0 * r, type="edge_circle")
        )

    # De-duplicate by (x, y, diameter) keeping the first occurrence.
    dedup: List[MountingHole] = []
    seen = set()
    for h in holes:
        key = (round(h.x, 4), round(h.y, 4), round(h.diameter, 4))
        if key not in seen:
            seen.add(key)
            dedup.append(h)
    return dedup


def _pad_diameter(pad: Sequence) -> Optional[float]:
    """Hole diameter for a pad; prefers ``drill``, falls back to ``size`` width.

    KiCad 6+ encodes a pad drill in three ways, all handled here:

    * ``(drill 3.2)``                -> 3.2  (circular)
    * ``(drill oval 1.0 2.0)``       -> 1.0  (slotted; minor width is the screw clearance)
    * ``(drill (offset 0.1 0) 3.2)`` -> 3.2  (circular, offset from the pad centre)

    For an offset drill the hole diameter is the token that follows the
    ``(offset X Y)`` sublist — the offset itself only shifts the hole within
    the pad and is irrelevant to the enclosure's mounting-hole diameter.
    """
    drill = _child(pad, "drill")
    if drill is not None and len(drill) >= 2:
        d1 = drill[1]
        if isinstance(d1, list):  # (drill (offset X Y) DIAM)
            if len(drill) >= 3:
                d = _f(drill[2])
                if d is not None and d > 0:
                    return d
        elif d1 == "oval":  # (drill oval W H)
            if len(drill) >= 3:
                d = _f(drill[2])
                if d is not None and d > 0:
                    return d
        else:
            d = _f(d1)
            if d is not None and d > 0:
                return d
    size = _child(pad, "size")
    if size is not None and len(size) >= 2:
        w = _f(size[1])
        if w is not None and w > 0:
            return w
    return None


# ---------------------------------------------------------------------------
# Footprint helpers
# ---------------------------------------------------------------------------

def _fp_at(fp: Sequence) -> Optional[Tuple[float, float, float, bool]]:
    """Return (x, y, angle_deg, flip_y) for a footprint, or None."""
    at = _child(fp, "at")
    if at is None or len(at) < 3:
        return None
    x = _f(at[1])
    y = _f(at[2])
    if x is None or y is None:
        return None
    angle = _f(at[3], 0.0) if len(at) >= 4 else 0.0
    layer = _child(fp, "layer")
    flip = False
    if layer is not None and len(layer) >= 2 and layer[1] == "B.Cu":
        flip = True
    return (x, y, angle, flip)


def _fp_ref_value_name(fp: Sequence) -> Tuple[str, str, str]:
    """Return (reference, value, footprint_name)."""
    name = fp[1] if len(fp) > 1 and isinstance(fp[1], str) else ""
    ref = ""
    value = ""
    for c in fp[1:]:
        if not isinstance(c, list) or not c:
            continue
        if c[0] == "fp_text":
            if len(c) >= 3 and c[1] == "reference" and isinstance(c[2], str):
                ref = c[2]
            elif len(c) >= 3 and c[1] == "value" and isinstance(c[2], str):
                value = c[2]
        elif c[0] == "property" and len(c) >= 3 and isinstance(c[2], str):
            if c[1] == "Reference" and not ref:
                ref = c[2]
            elif c[1] == "Value" and not value:
                value = c[2]
    if not ref:
        ref = name
    return ref, value, name


def _local_points_of(fp: Sequence, layer: str) -> List[Point]:
    """Collect footprint-local sample points from graphics on ``layer``."""
    pts: List[Point] = []
    for c in fp[1:]:
        if not isinstance(c, list) or not c:
            continue
        head = c[0]
        if head not in ("fp_line", "fp_rect", "fp_poly", "fp_circle", "fp_arc"):
            continue
        lc = _child(c, "layer")
        if lc is None or len(lc) < 2 or lc[1] != layer:
            continue
        if head == "fp_line":
            s = _xy(c, "start")
            e = _xy(c, "end")
            if s:
                pts.append(s)
            if e:
                pts.append(e)
        elif head == "fp_rect":
            s = _xy(c, "start")
            e = _xy(c, "end")
            if s and e:
                (x0, y0), (x1, y1) = s, e
                pts.extend([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        elif head == "fp_poly":
            pts_node = _child(c, "pts")
            if pts_node:
                for xy_node in pts_node[1:]:
                    if isinstance(xy_node, list) and xy_node and xy_node[0] == "xy" and len(xy_node) >= 3:
                        x = _f(xy_node[1])
                        y = _f(xy_node[2])
                        if x is not None and y is not None:
                            pts.append((x, y))
        elif head == "fp_circle":
            ctr = _xy(c, "center")
            end = _xy(c, "end")
            if ctr and end:
                r = geom.dist(ctr, end)
                pts.append(ctr)
                pts.extend([(ctr[0] + r, ctr[1]), (ctr[0] - r, ctr[1]),
                            (ctr[0], ctr[1] + r), (ctr[0], ctr[1] - r)])
        elif head == "fp_arc":
            pts.extend(_arc_from_node(c, 32))
    return pts


def _pad_extent_points(fp: Sequence) -> List[Point]:
    """Approximate footprint-local extents from pad positions + sizes."""
    pts: List[Point] = []
    for pad in _children(fp, "pad"):
        at = _xy(pad, "at")
        if at is None:
            continue
        w = h = 0.0
        size = _child(pad, "size")
        if size is not None and len(size) >= 3:
            w = _f(size[1], 0.0) or 0.0
            h = _f(size[2], 0.0) or 0.0
        # Pads may carry their own rotation.
        angle = 0.0
        pat = _child(pad, "at")
        if pat is not None and len(pat) >= 4:
            angle = _f(pat[3], 0.0) or 0.0
        corners = [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)]
        for c in corners:
            r = geom.rotate(c, angle)
            pts.append((at[0] + r[0], at[1] + r[1]))
    return pts


def _component_bbox(
    fp: Sequence,
    at: Tuple[float, float, float, bool],
    config: ParserConfig,
    ref: str,
) -> Tuple[Optional[BBox], Optional[Polygon], str]:
    """Compute absolute-space bbox + courtyard polygon, with fallback tiers."""
    fx, fy, fangle, flip = at
    crtyd_layer = "B.CrtYd" if flip else "F.CrtYd"
    silk_layer = "B.SilkS" if flip else "F.SilkS"
    fab_layer = "B.Fab" if flip else "F.Fab"

    def to_abs(local_pts: List[Point]) -> List[Point]:
        return [geom.transform(p, (fx, fy), fangle, flip_y=flip) for p in local_pts]

    # Tier 1: courtyard.
    local = _local_points_of(fp, crtyd_layer)
    if local:
        abs_pts = to_abs(local)
        hull = geom.convex_hull(abs_pts)
        bbox = BBox(min=geom.bbox_of(abs_pts)[0], max=geom.bbox_of(abs_pts)[1])
        return bbox, Polygon(points=hull), "courtyard"

    # Tier 2: pads + silk + fab.
    local = _pad_extent_points(fp)
    local += _local_points_of(fp, silk_layer)
    local += _local_points_of(fp, fab_layer)
    if local:
        abs_pts = to_abs(local)
        hull = geom.convex_hull(abs_pts)
        bbox = BBox(min=geom.bbox_of(abs_pts)[0], max=geom.bbox_of(abs_pts)[1])
        return bbox, Polygon(points=hull), "pads"

    # Tier 3: default square.
    if config.fallback_warnings:
        warnings.warn(f"footprint {ref!r} has no courtyard/pad/silk geometry; using {config.default_bbox_size} mm default")
    half = config.default_bbox_size / 2.0
    abs_pts = to_abs([(-half, -half), (half, -half), (half, half), (-half, half)])
    bbox = BBox(min=geom.bbox_of(abs_pts)[0], max=geom.bbox_of(abs_pts)[1])
    return bbox, Polygon(points=geom.convex_hull(abs_pts)), "default"


# ---------------------------------------------------------------------------
# Connector / cutout hints
# ---------------------------------------------------------------------------

def _resolve_hint(ref: str, value: str, fp_name: str, config: ParserConfig) -> str:
    if ref in config.cutout_overrides:
        return config.cutout_overrides[ref]
    haystack = " ".join([ref.lower(), value.lower(), fp_name.lower()])
    for hint, patterns in config.connector_patterns:
        for p in patterns:
            if p in haystack:
                return hint
    if ref and ref[0].upper() in ("J", "P"):
        return "header"
    return "none"


def _on_edge(outline: Polygon, bbox: Optional[BBox], tol: float) -> bool:
    if bbox is None or not outline.points:
        return False
    (x0, y0), (x1, y1) = bbox.min, bbox.max
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    poly = outline.points
    # Any corner outside the outline -> touching/crossing the edge.
    if any(not geom.point_in_polygon(c, poly) for c in corners):
        return True
    # Long thin parts can span the boundary with all corners inside; check
    # segment intersection between bbox edges and outline edges.
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    oedges = [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]
    for (a, b) in edges:
        for (c, d) in oedges:
            if geom.segments_intersect(a, b, c, d, tol=1e-6):
                return True
    return False


# ---------------------------------------------------------------------------
# Top-level parse
# ---------------------------------------------------------------------------

def parse(text: str, path: str = "", config: Optional[ParserConfig] = None) -> EnclosureData:
    cfg = config or ParserConfig()
    exprs = loads(text)
    top = None
    for e in exprs:
        if isinstance(e, list) and e and e[0] == "kicad_pcb":
            top = e
            break
    if top is None:
        raise ParseError("no (kicad_pcb ...) expression found")

    # kicad version
    version = None
    vc = _child(top, "version")
    if vc is not None and len(vc) >= 2:
        version = int(_f(vc[1], 0) or 0) or None

    # board thickness
    thickness = cfg.board_thickness_default
    general = _child(top, "general")
    if general is not None:
        tc = _child(general, "thickness")
        if tc is not None and len(tc) >= 2:
            t = _f(tc[1])
            if t is not None and t > 0:
                thickness = t

    # footprint nodes (top level) — KiCad 6+ calls them "footprint", KiCad 5 "module"
    footprints = [e for e in top[1:] if isinstance(e, list) and e and e[0] in ("footprint", "module")]

    # board outline + holes + edge circles
    outline, holes, edge_circles = _extract_outline(top[1:], cfg)

    # mounting holes
    mounting = _mounting_holes(footprints, outline, edge_circles, cfg)

    # components
    components: List[Component] = []
    for fp in footprints:
        ref, value, name = _fp_ref_value_name(fp)
        at = _fp_at(fp)
        if at is None:
            # A footprint without an (at ...) token; still keep it with a default bbox.
            at = (0.0, 0.0, 0.0, False)
        fx, fy, fangle, flip = at
        side = "B.Cu" if flip else "F.Cu"
        bbox, courtyard, src = _component_bbox(fp, at, cfg, ref)
        hint = _resolve_hint(ref, value, name, cfg)
        on_edge = _on_edge(outline, bbox, cfg.edge_tolerance)
        components.append(
            Component(
                ref=ref,
                value=value,
                side=side,
                x=round(fx, 3),
                y=round(fy, 3),
                rot=round(fangle, 3),
                bbox=bbox,
                courtyard=courtyard,
                height=cfg.component_height,
                cutout_hint=hint,
                on_edge=on_edge,
                footprint=name,
                bbox_source=src,
            )
        )

    data = EnclosureData(
        schema="enclosure-data-v1",
        units="mm",
        source=Source(path=path, generator="kicad", kicad_version=version),
        board=Board(thickness=thickness, outline=outline, holes=holes),
        mounting_holes=mounting,
        components=components,
        config={
            "component_height": cfg.component_height,
            "board_thickness_default": cfg.board_thickness_default,
            "edge_tolerance": cfg.edge_tolerance,
            "default_bbox_size": cfg.default_bbox_size,
        },
    )
    return data


def parse_bytes(data: bytes, path: str = "", config: Optional[ParserConfig] = None) -> EnclosureData:
    return parse(data.decode("utf-8"), path=path, config=config)


def parse_file(path: str, config: Optional[ParserConfig] = None) -> EnclosureData:
    with open(path, "r", encoding="utf-8") as f:
        return parse(f.read(), path=path, config=config)
