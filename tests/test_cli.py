"""End-to-end tests for the ``kicad-enclosure`` CLI.

Each test drives ``kicad_enclosure.cli.run`` (the testable, non-``os._exit``
entry point) against a real KiCad fixture and asserts on the produced files.

Run via ``tests/run_tests.py`` (or ``python -m unittest``), which wraps the run
with ``os._exit`` to dodge the OCP teardown segfault on Windows.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATA = os.path.join(ROOT, "tests", "data")
RECT = os.path.join(DATA, "rect_simple.kicad_pcb")
CONN = os.path.join(DATA, "connector_heavy.kicad_pcb")

from kicad_enclosure.cli import _build_parser, run  # noqa: E402


class CLITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="kicad_enclosure_cli_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _paths(self, name):
        return [os.path.join(self.tmp, f"{name}_{p}.{f}")
                for p in ("base", "lid") for f in ("step", "stl")]

    def test_help_documents_every_flag(self):
        help_text = _build_parser().format_help()
        for flag in (
            "--output", "--name", "--format", "--only-parse",
            "--wall-thickness", "--clearance", "--clearance-xy",
            "--floor-thickness", "--lid-thickness", "--standoff-height",
            "--fillet-radius", "--vent-count", "--vent-width",
            "--vent-length", "--vent-location", "--mount-style",
            "--cutout-clearance", "--cutout-config", "--cutout-override",
            "--component-height", "--board-thickness",
        ):
            self.assertIn(flag, help_text, f"missing {flag} in --help")

    def test_zero_config_produces_step_and_stl(self):
        code = run([RECT, "-o", self.tmp, "--name", "demo"])
        self.assertEqual(code, 0)
        for path in self._paths("demo"):
            self.assertTrue(os.path.exists(path), f"missing {path}")
            self.assertGreater(os.path.getsize(path), 0, f"empty {path}")

    def test_default_output_is_watertight(self):
        code = run([RECT, "-o", self.tmp, "--name", "solid"])
        self.assertEqual(code, 0)
        try:
            from kicad_enclosure.validation import check_stl_manifold
        except ImportError:
            self.skipTest("trimesh not available")
        for part in ("base", "lid"):
            diag = check_stl_manifold(os.path.join(self.tmp, f"solid_{part}.stl"))
            self.assertTrue(diag["watertight"], f"{part} not watertight: {diag}")
            self.assertGreater(diag["volume"], 0.0)

    def test_format_step_writes_only_step(self):
        code = run([RECT, "-o", self.tmp, "--name", "s", "--format", "step"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "s_base.step")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "s_lid.step")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "s_base.stl")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "s_lid.stl")))

    def test_format_stl_writes_only_stl(self):
        code = run([RECT, "-o", self.tmp, "--name", "t", "--format", "stl"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "t_base.step")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "t_base.stl")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "t_lid.stl")))

    def test_mount_style_and_vents_run(self):
        code = run([RECT, "-o", self.tmp, "--name", "m",
                    "--mount-style", "dual_tab",
                    "--vent-count", "6", "--vent-width", "4.0"])
        self.assertEqual(code, 0)
        for path in self._paths("m"):
            self.assertTrue(os.path.exists(path), f"missing {path}")

    def test_only_parse_emits_valid_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run([RECT, "--only-parse"])
        self.assertEqual(code, 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(doc["$schema"], "enclosure-data-v1")
        self.assertEqual(doc["units"], "mm")
        self.assertIn("board", doc)
        self.assertGreater(len(doc["board"]["outline"]["points"]), 2)

    def test_cutout_config_override_wins(self):
        cfg = {"cutout_overrides": {"J1": "barrel"}}
        cfg_path = os.path.join(self.tmp, "cutouts.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = run([CONN, "--cutout-config", cfg_path, "--only-parse"])
        self.assertEqual(code, 0)
        doc = json.loads(buf.getvalue())
        hints = {c["ref"]: c["cutout_hint"] for c in doc["components"]}
        self.assertEqual(hints.get("J1"), "barrel")

    def test_missing_board_exits_2(self):
        buf = io.StringIO()
        from contextlib import redirect_stderr
        with redirect_stderr(buf):
            code = run([os.path.join(self.tmp, "nope.kicad_pcb")])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
