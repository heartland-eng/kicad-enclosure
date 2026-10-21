"""Generate PNG previews of enclosures for the README.

For each fixture: parse -> generate -> export STL -> render orthographic
projections with matplotlib/trimesh.  Outputs PNGs into ``docs/``.

Usage (from the repo root)::

    python scripts/render_previews.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import json  # noqa: E402
import tempfile  # noqa: E402

import numpy as np  # noqa: E402
import trimesh  # noqa: E402
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

from kicad_parser import parse_file, to_dict  # noqa: E402
from kicad_enclosure import export_enclosure, generate_from_dict  # noqa: E402

DATA = os.path.join(ROOT, "tests", "data")
DOCS = os.path.join(ROOT, "docs")

FIXTURES = [
    ("rect_simple", "rect_simple.kicad_pcb"),
    ("connector_heavy", "connector_heavy.kicad_pcb"),
]


def _project(ax, verts, faces, view, color, alpha=0.85):
    v = verts[:, list(view)]
    polys = [v[f] for f in faces]
    coll = Poly3DCollection([[list(p) for p in poly] for poly in polys], alpha=alpha)
    coll.set_facecolor(color)
    coll.set_edgecolor("k")
    coll.set_linewidth(0.05)
    ax.add_collection3d(coll)
    ax.set_aspect("equal")
    return coll


def _limits(ax, verts):
    mn = verts.min(axis=0)
    mx = verts.max(axis=0)
    c = (mn + mx) / 2
    r = (mx - mn).max() / 2 * 1.15
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - r, c[2] + r)


def render(name, board_file):
    data = parse_file(os.path.join(DATA, board_file))
    doc = to_dict(data)
    result = generate_from_dict(doc)

    with tempfile.TemporaryDirectory() as tmp:
        manifest = export_enclosure(result.base, result.lid, tmp, name)
        base = trimesh.load(manifest["base_stl"], force="mesh")
        lid = trimesh.load(manifest["lid_stl"], force="mesh")

    bv, bf = np.asarray(base.vertices), np.asarray(base.faces)
    lv, lf = np.asarray(lid.vertices), np.asarray(lid.faces)

    fig = plt.figure(figsize=(14, 4.6))

    # 1. base + lid, assembled isometric
    ax1 = fig.add_subplot(1, 3, 1, projection="3d")
    _project(ax1, bv, bf, (0, 1, 2), "#8ab4d8")
    _project(ax1, lv, lf, (0, 1, 2), "#f4a259", alpha=0.9)
    ax1.set_title("base + lid (isometric)")
    ax1.view_init(elev=28, azim=-60)
    _limits(ax1, np.vstack([bv, lv]))

    # 2. front view (XZ) showing connector cutouts
    ax2 = fig.add_subplot(1, 3, 2, projection="3d")
    _project(ax2, bv, bf, (0, 2, 1), "#8ab4d8")
    _project(ax2, lv, lf, (0, 2, 1), "#f4a259", alpha=0.9)
    ax2.set_title("front (cutouts)")
    ax2.view_init(elev=0, azim=-90)
    _limits(ax2, np.vstack([bv, lv]))

    # 3. top view (XY)
    ax3 = fig.add_subplot(1, 3, 3, projection="3d")
    _project(ax3, bv, bf, (0, 1, 2), "#8ab4d8")
    _project(ax3, lv, lf, (0, 1, 2), "#f4a259", alpha=0.9)
    ax3.set_title("top")
    ax3.view_init(elev=90, azim=-90)
    _limits(ax3, np.vstack([bv, lv]))

    out = os.path.join(DOCS, f"{name}_preview.png")
    fig.tight_layout()
    fig.savefig(out, dpi=110, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out)
    return out


def main():
    os.makedirs(DOCS, exist_ok=True)
    for name, board in FIXTURES:
        render(name, board)


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
