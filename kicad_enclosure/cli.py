"""``kicad-enclosure`` command-line interface.

Turns a KiCad ``.kicad_pcb`` board file into a printable enclosure (a base and
a removable lid), exported as STEP and/or STL.

Pipeline
--------
1. Parse the PCB with :mod:`kicad_parser` into an ``enclosure-data-v1`` document.
2. Generate base + lid solids with :mod:`kicad_enclosure` (build123d).
3. Export STEP (editable master) and/or STL (slicer-ready) files.

``kicad-enclosure board.kicad_pcb`` is enough to produce a usable enclosure
with zero configuration — every knob has a sensible default.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional

FORMATS = ("step", "stl", "both")
MOUNT_STYLES = ("none", "dual_tab", "din_rail")
VENT_LOCATIONS = ("walls", "lid", "both")


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="kicad-enclosure",
        description="Generate a printable enclosure (base + lid) from a KiCad "
        ".kicad_pcb board file.",
        epilog="Example: kicad-enclosure board.kicad_pcb --format both",
    )
    ap.add_argument(
        "board",
        help="path to a .kicad_pcb file",
    )
    ap.add_argument(
        "-o", "--output", default=".", metavar="DIR",
        help="directory to write output files into (default: current directory)",
    )
    ap.add_argument(
        "--name", default=None, metavar="NAME",
        help="base name for the output files (default: the board file name)",
    )
    ap.add_argument(
        "--format", choices=FORMATS, default="both",
        help="formats to export: step, stl, or both (default: both)",
    )
    ap.add_argument(
        "--only-parse", action="store_true",
        help="parse the board and print enclosure-data-v1 JSON to stdout, then exit",
    )

    g_geom = ap.add_argument_group("geometry")
    g_geom.add_argument("--wall-thickness", type=float, default=None,
                        help="wall shell thickness in mm (default: 2.0)")
    g_geom.add_argument("--clearance", type=float, default=None,
                        help="air gap above the tallest component (Z) in mm (default: 2.0)")
    g_geom.add_argument("--clearance-xy", type=float, default=None,
                        help="board-to-wall clearance in X/Y in mm (default: 0.4)")
    g_geom.add_argument("--floor-thickness", type=float, default=None,
                        help="base floor thickness in mm (default: 2.0)")
    g_geom.add_argument("--lid-thickness", type=float, default=None,
                        help="lid top-plate thickness in mm (default: 2.0)")
    g_geom.add_argument("--standoff-height", type=float, default=None,
                        help="board standoff height in mm (default: 5.0)")
    g_geom.add_argument("--fillet-radius", type=float, default=None,
                        help="outer vertical edge fillet radius in mm (default: 0.5; 0 disables)")

    g_vent = ap.add_argument_group("vents")
    g_vent.add_argument("--vent-count", type=int, default=None,
                        help="vent slots per wall (default: 5; 0 disables)")
    g_vent.add_argument("--vent-width", type=float, default=None,
                        help="vent slot width (short dimension) in mm (default: 3.0)")
    g_vent.add_argument("--vent-length", type=float, default=None,
                        help="vent slot length (long dimension) in mm (default: 20.0)")
    g_vent.add_argument("--vent-location", choices=VENT_LOCATIONS, default=None,
                        help="where to place vents: walls, lid, or both (default: walls)")

    g_mount = ap.add_argument_group("wall mounting")
    g_mount.add_argument("--mount-style", choices=MOUNT_STYLES, default=None,
                         help="wall-mount style: none, dual_tab, or din_rail (default: none)")

    g_cut = ap.add_argument_group("connector cutouts")
    g_cut.add_argument("--cutout-clearance", type=float, default=None,
                       help="extra margin around connector openings in mm (default: 0.5)")
    g_cut.add_argument("--cutout-config", default=None, metavar="FILE",
                       help="JSON file mapping reference designators to cutout hints "
                            "(see README for the format)")
    g_cut.add_argument("--cutout-override", action="append", default=[], metavar="REF=HINT",
                       help="map a reference designator to a cutout hint (repeatable)")

    g_parse = ap.add_argument_group("parsing")
    g_parse.add_argument("--component-height", type=float, default=5.0,
                         help="default component height in mm (KiCad stores none; default: 5.0)")
    g_parse.add_argument("--board-thickness", type=float, default=1.6,
                         help="fallback board thickness in mm when the file omits it (default: 1.6)")

    return ap


def _default_name(board_path: str) -> str:
    stem = os.path.splitext(os.path.basename(board_path))[0]
    return stem or "enclosure"


def _load_cutout_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        raise ValueError("cutout config must be a JSON object")
    return doc


def _collect_overrides(args) -> Dict[str, str]:
    """Merge --cutout-config cutout_overrides and --cutout-override flags.

    Returns a ``{ref: cutout_hint}`` mapping.  Raises ``ValueError`` on a
    malformed flag so ``run`` can report it cleanly.
    """
    overrides: Dict[str, str] = {}
    if args.cutout_config:
        cfg = _load_cutout_config(args.cutout_config)
        raw = cfg.get("cutout_overrides", {})
        if not isinstance(raw, dict):
            raise ValueError("--cutout-config: 'cutout_overrides' must be an object")
        overrides.update({str(k): str(v) for k, v in raw.items()})
    for item in args.cutout_override:
        if "=" not in item:
            raise ValueError(f"--cutout-override expects REF=HINT, got {item!r}")
        ref, hint = item.split("=", 1)
        overrides[ref.strip()] = hint.strip()
    return overrides


def _collect_params(args, cutout_config: dict) -> Dict[str, float]:
    """Build the ``EnclosureParams`` overrides from the CLI flags.

    The cutout-config file's ``config`` object is merged first; explicitly set
    CLI flags then win over it.
    """
    params: dict = {}
    if isinstance(cutout_config.get("config"), dict):
        params.update(cutout_config["config"])

    flag_map = {
        "wall_thickness": args.wall_thickness,
        "clearance_z": args.clearance,
        "clearance_xy": args.clearance_xy,
        "floor_thickness": args.floor_thickness,
        "lid_thickness": args.lid_thickness,
        "standoff_height": args.standoff_height,
        "fillet_radius": args.fillet_radius,
        "vent_count": args.vent_count,
        "vent_width": args.vent_width,
        "vent_length": args.vent_length,
        "vent_location": args.vent_location,
        "mount_style": args.mount_style,
        "cutout_clearance": args.cutout_clearance,
    }
    for key, value in flag_map.items():
        if value is not None:
            params[key] = value
    return params


def run(argv: Optional[List[str]] = None) -> int:
    """Execute the CLI and return a process exit code (0 / 1 / 2).

    Kept free of ``os._exit`` so it is directly testable; the ``main`` wrapper
    adds the ``os._exit`` guard that avoids the OCP teardown segfault.
    """
    args = _build_parser().parse_args(argv)

    # --- connector cutout config + overrides ---------------------------------
    cutout_config: dict = {}
    if args.cutout_config:
        try:
            cutout_config = _load_cutout_config(args.cutout_config)
        except (OSError, ValueError) as exc:
            print(f"error: --cutout-config: {exc}", file=sys.stderr)
            return 2
    try:
        overrides = _collect_overrides(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # --- parse the board -----------------------------------------------------
    from kicad_parser import ParserConfig, parse_file, to_dict

    cfg = ParserConfig(
        component_height=args.component_height,
        board_thickness_default=args.board_thickness,
        cutout_overrides=overrides,
    )
    try:
        data = parse_file(args.board, config=cfg)
    except Exception as exc:  # noqa: BLE001 — surface a clean error + exit 2
        print(f"error: failed to parse {args.board!r}: {exc}", file=sys.stderr)
        return 2

    if args.only_parse:
        print(json.dumps(to_dict(data), indent=2))
        return 0

    doc = to_dict(data)

    # --- generate ------------------------------------------------------------
    from kicad_enclosure import export_enclosure, generate_from_dict

    params = _collect_params(args, cutout_config)
    result = generate_from_dict(doc, params)

    name = args.name or _default_name(args.board)
    out_dir = args.output or "."

    # --- export (format-aware) ------------------------------------------------
    manifest = export_enclosure(result.base, result.lid, out_dir, name)
    written: List[str] = []
    for part in ("base", "lid"):
        for fmt in ("step", "stl"):
            key = f"{part}_{fmt}"
            path = manifest[key]
            if args.format in ("both", fmt):
                written.append(path)
            else:
                os.remove(path)

    print(f"generated {name} enclosure -> {os.path.abspath(out_dir)}")
    for path in written:
        print(f"  {path}")
    print(f"  wall height: {result.wall_height:.2f} mm")
    return 0


def main(argv: Optional[List[str]] = None) -> None:
    code = run(argv)
    sys.stdout.flush()
    sys.stderr.flush()
    # os._exit dodges the OCP (OpenCascade) segfault at Python teardown on
    # Windows (exit 139) that fires even after every export has succeeded.
    os._exit(code)


if __name__ == "__main__":
    main()
