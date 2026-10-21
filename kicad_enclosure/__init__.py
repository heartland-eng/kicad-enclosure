"""kicad-enclosure: parametric build123d enclosure generator.

Consumes parsed board geometry (``enclosure-data-v1`` JSON) and produces a
parametric base + lid pair with standoffs, vents, connector cutouts and
wall-mount options.
"""

from .data import (
    BoardData,
    Component,
    EnclosureParams,
    MountingHole,
    board_from_dict,
    params_from_dict,
)
from .generator import EnclosureResult, generate, generate_from_dict
from .export import export_enclosure, export_part
from .validation import check_component_clearance, check_stl_manifold

__all__ = [
    "BoardData",
    "Component",
    "EnclosureParams",
    "MountingHole",
    "board_from_dict",
    "params_from_dict",
    "EnclosureResult",
    "generate",
    "generate_from_dict",
    "export_enclosure",
    "export_part",
    "check_component_clearance",
    "check_stl_manifold",
]
