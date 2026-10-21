"""Unit tests for the ``kicad_parser`` drill-diameter extraction.

These are standard-library-only (no build123d/trimesh required), so they run
fast in any Python >= 3.8 environment and guard the mounting-hole detection
independent of the enclosure generator.

Run with::

    python -m unittest tests.test_parser
    # or, from the repo root:
    python -m unittest discover -s tests -p 'test_parser.py'
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from kicad_parser import parse  # noqa: E402
from kicad_parser.parser import _pad_diameter  # noqa: E402


def _pad(drill_tokens):
    """Build a minimal np_thru_hole pad node carrying the given ``drill`` list."""
    return ["pad", "1", "np_thru_hole", "circle",
            ["at", "0", "0"], ["size", "3.2", "3.2"], ["drill"] + drill_tokens]


class PadDiameterTest(unittest.TestCase):
    def test_plain_circular_drill(self):
        self.assertEqual(_pad_diameter(_pad(["3.2"])), 3.2)

    def test_offset_drill_zero_offset(self):
        # (drill (offset 0 0) 3.2) — the diameter is 3.2, not the offset.
        pad = _pad([["offset", "0", "0"], "3.2"])
        self.assertEqual(_pad_diameter(pad), 3.2)

    def test_offset_drill_nonzero_offset(self):
        # (drill (offset 0.5 0.5) 2.5) — must NOT return 2*|offset|.
        pad = _pad([["offset", "0.5", "0.5"], "2.5"])
        self.assertEqual(_pad_diameter(pad), 2.5)

    def test_oval_drill_uses_minor_width(self):
        # (drill oval 1.0 2.0) — slotted hole; screw clearance is the width.
        pad = _pad(["oval", "1.0", "2.0"])
        self.assertEqual(_pad_diameter(pad), 1.0)

    def test_no_drill_falls_back_to_size_width(self):
        pad = ["pad", "1", "np_thru_hole", "circle",
               ["at", "0", "0"], ["size", "2.5", "2.5"]]
        self.assertEqual(_pad_diameter(pad), 2.5)

    def test_no_drill_no_size_returns_none(self):
        pad = ["pad", "1", "np_thru_hole", "circle", ["at", "0", "0"]]
        self.assertIsNone(_pad_diameter(pad))


OFFSET_DRILL_BOARD = """(kicad_pcb (version 20240108) (generator "pcbnew")
  (general (thickness 1.6))
  (footprint "MountingHole_3.2mm_M3" (layer "F.Cu")
    (at 5 5)
    (property "Reference" "H1" (at 0 0) (layer "F.SilkS") (uuid "00000000-0000-0000-0000-000000000001"))
    (pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill (offset 0 0) 3.2) (layers "*.Cu" "*.Mask") (uuid "00000000-0000-0000-0000-000000000101"))
  )
  (gr_line (start 0 0) (end 20 0) (layer "Edge.Cuts") (stroke (width 0.1) (type default)) (uuid "00000000-0000-0000-0000-000000000301"))
  (gr_line (start 20 0) (end 20 20) (layer "Edge.Cuts") (stroke (width 0.1) (type default)) (uuid "00000000-0000-0000-0000-000000000302"))
  (gr_line (start 20 20) (end 0 20) (layer "Edge.Cuts") (stroke (width 0.1) (type default)) (uuid "00000000-0000-0000-0000-000000000303"))
  (gr_line (start 0 20) (end 0 0) (layer "Edge.Cuts") (stroke (width 0.1) (type default)) (uuid "00000000-0000-0000-0000-000000000304"))
)
"""


class OffsetDrillBoardTest(unittest.TestCase):
    def test_offset_drill_mounting_hole_is_kept_with_correct_diameter(self):
        data = parse(OFFSET_DRILL_BOARD)
        holes = data.mounting_holes
        self.assertEqual(len(holes), 1, "offset-drill mounting hole must not be dropped")
        self.assertAlmostEqual(holes[0].diameter, 3.2, places=4)
        self.assertEqual(holes[0].type, "npth_pad")


if __name__ == "__main__":
    unittest.main(verbosity=2)
