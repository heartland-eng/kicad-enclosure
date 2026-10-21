# enclosure-data-v1 JSON contract

The intermediate document shared by `kicad_parser` (producer) and
`kicad_enclosure` (consumer). All coordinates and sizes are millimetres; angles
are degrees.

```jsonc
{
  "$schema": "enclosure-data-v1",
  "units": "mm",
  "source": { "path": "board.kicad_pcb", "generator": "kicad", "kicad_version": 20240108 },
  "board": {
    "thickness": 1.6,
    "outline": { "points": [[0,0], [50,0], [50,30], [0,30]] },
    "holes": [ { "points": [[10,10], [14,10], [14,14], [10,14]] } ]
  },
  "mounting_holes": [ { "x": 2.5, "y": 2.5, "diameter": 3.2, "type": "npth_pad" } ],
  "components": [
    {
      "ref": "J1", "value": "USB_C", "side": "F.Cu",
      "x": 25.0, "y": 0.0, "rot": 90,
      "bbox": { "min": [24.0, -4.0], "max": [26.0, 4.0] },
      "courtyard": { "points": [[24.0,-4.0],[26.0,-4.0],[26.0,4.0],[24.0,4.0]] },
      "height": 5.0, "cutout_hint": "usb_c", "on_edge": true,
      "footprint": "Connector_USB:USB_C_Receptacle_USB2.0", "bbox_source": "courtyard"
    }
  ],
  "config": { "component_height": 5.0, "board_thickness_default": 1.6 }
}
```

## Fields

- `board.outline` — closed outer boundary ring (CCW), points `[x, y]`.
- `board.holes` — interior cutouts, each a ring of points.
- `mounting_holes[]` — absolute `x`/`y`, `diameter`, and `type`
  (`npth_pad` | `edge_circle`).
- `components[]` — per-part placement and geometry:
  - `bbox` — axis-aligned box `{min:[x,y], max:[x,y]}` in absolute space.
  - `courtyard` — rotated footprint outline (preferred over `bbox` for
    clearance checks).
  - `cutout_hint` — `none`, `usb_a`, `usb_c`, `usb_micro`, `rj45`, `hdmi`,
    `display`, `sd`, `barrel`, `header`, or a custom string.
  - `bbox_source` — `courtyard` → `pads` → `default` (fallback tier).
- `config` — optional defaults carried through from the parser.

The consumer is tolerant: missing keys fall back to defaults, and both a
top-level and a `board`-nested layout are accepted for the shared fields.
