"""STEP + STL export helpers for the generated enclosure."""

from __future__ import annotations

import os
from typing import Tuple

import build123d as b3d


def export_part(shape, path_step: str, path_stl: str) -> Tuple[str, str]:
    """Write one shape as both STEP (editable master) and STL (slicer-ready).

    Returns the two written paths.
    """
    b3d.export_step(shape, path_step)
    b3d.export_stl(shape, path_stl)
    return path_step, path_stl


def export_enclosure(base, lid, out_dir: str, name: str) -> dict:
    """Export base and lid as separate STEP+STL files into ``out_dir``.

    Returns a manifest mapping each deliverable kind to its absolute path.
    """
    os.makedirs(out_dir, exist_ok=True)
    base_step = os.path.join(out_dir, f"{name}_base.step")
    base_stl = os.path.join(out_dir, f"{name}_base.stl")
    lid_step = os.path.join(out_dir, f"{name}_lid.step")
    lid_stl = os.path.join(out_dir, f"{name}_lid.stl")

    export_part(base, base_step, base_stl)
    export_part(lid, lid_step, lid_stl)

    return {
        "base_step": base_step,
        "base_stl": base_stl,
        "lid_step": lid_step,
        "lid_stl": lid_stl,
    }
