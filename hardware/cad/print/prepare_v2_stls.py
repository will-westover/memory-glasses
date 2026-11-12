#!/usr/bin/env python3
"""Lays out the raw v2 STLs (from Fusion's MemoryGlassesExport script) for printing and checks every mesh.

Each part is rotated to its print orientation, centred, and set on z=0, then written to hardware/cad/print/v2/.
Run from anywhere: python3 hardware/cad/print/prepare_v2_stls.py
"""

import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "v2", "_raw")
OUT = os.path.join(HERE, "v2")
REPORT = os.path.join(OUT, "mesh-check.txt")

# rows map model (x, y, z) to print (x, y, z)
SAME = np.eye(3)
X_UP = np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], float)     # model -x face on the bed
X_DOWN = np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], float)   # model +x face on the bed
Y_UP = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float)     # model -y face on the bed
Y_DOWN = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], float)   # model +y face on the bed
FLIP = np.array([[1, 0, 0], [0, -1, 0], [0, 0, -1]], float)    # model top on the bed

# raw name, final name, quantity, orientation, volume from Fusion's check report (mm3)
PARTS = [
    ("front-unit", "01-front-unit", 1, SAME, 31761.7),
    ("left-arm", "02-left-arm", 1, X_UP, 17186.7),
    ("right-arm", "03-right-arm", 1, X_DOWN, 14037.5),
    ("battery-lid", "04-battery-lid", 1, X_DOWN, 1656.2),
    ("camera-cap", "05-camera-cap", 1, SAME, 625.2),
    ("cube-lens-cap", "06-cube-lens-cap", 1, FLIP, 3839.3),
    ("tunnel-lid", "07-tunnel-lid", 1, SAME, 1912.2),
    ("display-sled", "08-display-sled", 1, X_UP, 1139.7),
    ("spacer-front", "09-focus-spacer-front", 2, SAME, 175.1),
    ("spacer-rear", "10-focus-spacer-rear", 2, SAME, 35.1),
    ("wire-cover-strip", "11-wire-cover-strip", 1, Y_UP, 243.4),
    # all six tabs are the same 6 x 2.5 x 1 rectangle
    ("tab", "12-lens-retainer-tab", 6, Y_UP, 15.0),
    ("tinted-window", "13-tinted-window-optional", 1, Y_UP, 81.9),
]

FRONT_ORIENTATIONS = [("standing as worn", SAME), ("upside down (brow on bed)", FLIP),
                      ("face down (+y on bed)", Y_DOWN), ("back down (-y on bed)", Y_UP)]


def read_stl(path):
    with open(path, "rb") as f:
        f.seek(80)
        n = struct.unpack("<I", f.read(4))[0]
        data = np.frombuffer(f.read(50 * n), dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]), count=n)
    return data["v"].reshape(-1, 3, 3).astype(float)


def write_stl(path, tris, header):
    e1, e2 = tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    normals = np.cross(e1, e2)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = normals / np.where(lengths == 0, 1, lengths)
    rec = np.zeros(len(tris), dtype=np.dtype([("n", "<3f4"), ("v", "<9f4"), ("a", "<u2")]))
    rec["n"] = normals
    rec["v"] = tris.reshape(-1, 9)
    with open(path, "wb") as f:
        f.write(header.encode()[:80].ljust(80, b" "))
        f.write(struct.pack("<I", len(tris)))
        f.write(rec.tobytes())


def place(tris, rotation):
    out = tris @ rotation.T
    lo, hi = out.reshape(-1, 3).min(0), out.reshape(-1, 3).max(0)
    out = out - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
    return out


def topology(tris):
    """(open or mismatched edges, connected pieces), welding vertices to 0.1 micron."""
    keys = np.round(tris.reshape(-1, 3) * 1e4).astype(np.int64)
    _, ids = np.unique(keys, axis=0, return_inverse=True)
    ids = ids.reshape(-1, 3)
    edges = {}
    parent = list(range(ids.max() + 1))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b, c in ids.tolist():
        for u, v in ((a, b), (b, c), (c, a)):
            edges[(u, v)] = edges.get((u, v), 0) + 1
            parent[find(u)] = find(v)
    bad = sum(1 for (u, v), k in edges.items() if edges.get((v, u), 0) != k)
    return bad, len({find(i) for i in set(ids.ravel().tolist())})


def stats(tris):
    e = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    area = np.linalg.norm(e, axis=1) / 2
    nz = np.divide(e[:, 2], 2 * area, out=np.zeros_like(area), where=area > 0)
    zmin = tris[:, :, 2].min(1)
    zmax = tris[:, :, 2].max(1)
    # 0.1 tolerance: the brow's fitted curve overshoots its plane by ~0.03, which the first layer absorbs
    on_bed = (zmax < 0.1) & (nz < -0.99)
    # faces more than 45 degrees past vertical and off the bed are what a slicer would support
    overhang = (nz < -0.7071) & ~((zmax < 0.3))
    volume = float(np.einsum("ij,ij->i", tris[:, 0], np.cross(tris[:, 1], tris[:, 2])).sum() / 6)
    return volume, float(area[on_bed].sum()), float(area[overhang].sum())


def main():
    if not os.path.isdir(RAW):
        sys.exit("No raw STLs at {}; run MemoryGlassesExport in Fusion first.".format(RAW))
    lines = ["{:32s} {:>3s} {:>22s} {:>9s} {:>8s} {:>6s} {:>6s} {:>7s} {:>8s}".format(
        "file", "qty", "size x y z (mm)", "vol mm3", "vs CAD", "open", "pieces", "bed mm2", "over mm2")]
    problems = []
    for raw, final, qty, rotation, cad_volume in PARTS:
        tris = place(read_stl(os.path.join(RAW, raw + ".stl")), rotation)
        size = tris.reshape(-1, 3).max(0) - tris.reshape(-1, 3).min(0)
        volume, bed, over = stats(tris)
        bad, pieces = topology(tris)
        drift = (volume - cad_volume) / cad_volume * 100
        name = "{}-x{}.stl".format(final, qty)
        write_stl(os.path.join(OUT, name), tris, "Memory Glasses v2 {}".format(final))
        lines.append("{:32s} {:3d} {:6.1f} {:6.1f} {:6.1f} {:9.1f} {:7.2f}% {:6d} {:6d} {:7.0f} {:8.0f}".format(
            final, qty, size[0], size[1], size[2], volume, drift, bad, pieces, bed, over))
        if bad or pieces != 1 or abs(drift) > 2:
            problems.append(final)
    lines += ["", "PROBLEMS: " + (", ".join(problems) if problems else "none"), "",
              "FRONT UNIT ORIENTATIONS (bed contact / area a slicer would support, mm2)"]
    front = read_stl(os.path.join(RAW, "front-unit.stl"))
    for label, rotation in FRONT_ORIENTATIONS:
        tris = place(front, rotation)
        _, bed, over = stats(tris)
        size = tris.reshape(-1, 3).max(0) - tris.reshape(-1, 3).min(0)
        lines.append("{:28s} bed {:6.0f}  overhang {:6.0f}  size {:.0f} x {:.0f} x {:.0f}".format(
            label, bed, over, size[0], size[1], size[2]))
    text = "\n".join(lines) + "\n"
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
