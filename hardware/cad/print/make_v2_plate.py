#!/usr/bin/env python3
"""Packs every v2 print file onto one 220 x 220 mm bed as a single STL, for printing everything in one go.

Reads the oriented STLs in hardware/cad/print/v2/ (from prepare_v2_stls.py) and only turns parts about the vertical axis, so
each keeps its print orientation. Writes hardware/cad/print/v2/ready-to-print/memory-glasses-plate.stl.
Run: uv run --with numpy python hardware/cad/print/make_v2_plate.py
"""

import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "v2")
OUT_DIR = os.path.join(SRC, "ready-to-print")
OUT = os.path.join(OUT_DIR, "memory-glasses-plate.stl")
BED = 220.0
GAP = 5.0

# file, turned 90 degrees on the bed, (x, y) of the part's front-left corner. The tinted window is left off: it only
# works printed in dark filament.
LAYOUT = [
    ("11-wire-cover-strip-x1", False, (0, 0)),
    ("12-lens-retainer-tab-x6", False, [(140 + 11 * i, 0) for i in range(6)]),
    ("04-battery-lid-x1", True, (0, 17)),
    ("05-camera-cap-x1", False, (74, 17)),
    ("07-tunnel-lid-x1", False, (101, 17)),
    ("08-display-sled-x1", False, (137, 17)),
    ("06-cube-lens-cap-x1", False, (172, 17)),
    ("02-left-arm-x1", True, (0, 57)),
    ("09-focus-spacer-front-x2", False, [(137, 57), (137, 65)]),
    ("10-focus-spacer-rear-x2", False, [(162, 57), (162, 65)]),
    ("03-right-arm-x1", True, (0, 91)),
    ("01-front-unit-x1", False, (0, 125)),
]


def read_stl(path):
    with open(path, "rb") as f:
        data = f.read()
    count = struct.unpack_from("<I", data, 80)[0]
    rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=count, offset=84)
    return rec["v"].astype(np.float64)


def write_stl(path, tris):
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    rec = np.zeros(len(tris), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    rec["n"], rec["v"] = normals, tris
    with open(path, "wb") as f:
        f.write(b"Memory Glasses v2 - all parts".ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        f.write(rec.tobytes())


def main():
    placed, boxes = [], []
    for name, turned, spots in LAYOUT:
        tris = read_stl(os.path.join(SRC, name + ".stl"))
        if turned:
            tris = tris[..., [1, 0, 2]] * np.array([-1.0, 1.0, 1.0])
        lo = tris.reshape(-1, 3).min(axis=0)
        for x, y in spots if isinstance(spots, list) else [spots]:
            part = tris - lo + np.array([x, y, 0.0])
            placed.append(part)
            flat = part.reshape(-1, 3)
            boxes.append((name, flat[:, 0].min(), flat[:, 0].max(), flat[:, 1].min(), flat[:, 1].max()))
    problems = []
    for i, (na, ax0, ax1, ay0, ay1) in enumerate(boxes):
        for nb, bx0, bx1, by0, by1 in boxes[i + 1:]:
            if ax0 < bx1 + GAP and bx0 < ax1 + GAP and ay0 < by1 + GAP and by0 < ay1 + GAP:
                problems.append("{} is within {} mm of {}".format(na, GAP, nb))
    tris = np.concatenate(placed)
    flat = tris.reshape(-1, 3)
    size = flat.max(axis=0) - flat.min(axis=0)
    if size[0] > BED - 10 or size[1] > BED - 10:
        problems.append("layout is {:.0f} x {:.0f} mm, too big for a {:.0f} mm bed".format(size[0], size[1], BED))
    if problems:
        sys.exit("\n".join(problems))
    # centre on the bed so the file drops straight onto the plate
    tris = tris - np.array([flat[:, 0].min() + size[0] / 2 - BED / 2, flat[:, 1].min() + size[1] / 2 - BED / 2, flat[:, 2].min()])
    os.makedirs(OUT_DIR, exist_ok=True)
    write_stl(OUT, tris)
    print("{} parts, {:.0f} x {:.0f} x {:.0f} mm, {} triangles -> {}".format(len(boxes), size[0], size[1], size[2], len(tris), OUT))


if __name__ == "__main__":
    main()
