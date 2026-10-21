"""Command-line entry: ``python -m kicad_parser board.kicad_pcb``.

Emits the ``enclosure-data-v1`` JSON document to stdout (mirrors the
``--only-parse`` behaviour of the eventual ``kicad-enclosure`` CLI).
"""

import argparse
import json
import sys

from .model import to_dict
from .parser import ParserConfig, parse_file


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="kicad_parser",
        description="Extract board geometry from a KiCad .kicad_pcb file as enclosure-data-v1 JSON.",
    )
    ap.add_argument("board", help="path to a .kicad_pcb file")
    ap.add_argument(
        "--component-height", type=float, default=5.0,
        help="default component height in mm (KiCad stores no height)",
    )
    ap.add_argument(
        "--board-thickness", type=float, default=1.6,
        help="fallback board thickness in mm when the file omits it",
    )
    ap.add_argument(
        "--cutout-override", action="append", default=[],
        metavar="REF=HINT",
        help="map a reference designator to a cutout hint (repeatable)",
    )
    args = ap.parse_args(argv)

    overrides = {}
    for item in args.cutout_override:
        if "=" in item:
            ref, hint = item.split("=", 1)
            overrides[ref.strip()] = hint.strip()

    cfg = ParserConfig(
        component_height=args.component_height,
        board_thickness_default=args.board_thickness,
        cutout_overrides=overrides,
    )
    try:
        data = parse_file(args.board, config=cfg)
    except Exception as exc:  # noqa: BLE001 - surface a clean error + exit 2
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(to_dict(data), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
