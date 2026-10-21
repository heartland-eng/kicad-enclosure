"""kicad_parser — dependency-free KiCad ``.kicad_pcb`` geometry extraction.

Parses a KiCad PCB file into a typed :class:`EnclosureData` structure covering
the board outline, mounting holes, component bounding boxes, and connector /
cutout hints.  No KiCad installation or third-party package is required.

Typical usage::

    from kicad_parser import parse_file, to_dict
    data = parse_file("board.kicad_pcb")
    print(json.dumps(to_dict(data), indent=2))
"""

from .model import (
    BBox,
    Board,
    Component,
    EnclosureData,
    MountingHole,
    Point,
    Polygon,
    Source,
    from_dict,
    to_dict,
)
from .parser import ParserConfig, ParseError, parse, parse_bytes, parse_file

__all__ = [
    "BBox",
    "Board",
    "Component",
    "EnclosureData",
    "MountingHole",
    "Point",
    "Polygon",
    "Source",
    "ParserConfig",
    "ParseError",
    "parse",
    "parse_bytes",
    "parse_file",
    "from_dict",
    "to_dict",
]

__version__ = "0.1.0"
