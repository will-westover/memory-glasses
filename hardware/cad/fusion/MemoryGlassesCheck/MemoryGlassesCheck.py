"""Inspects the active MemoryGlasses design: every body's size, plus every pair of solid parts that overlap.

Writes ~/projects/memory-glasses/hardware/cad/reports/check-report.txt so the results can be read outside Fusion.
"""

import itertools
import os
import traceback

import adsk.core
import adsk.fusion

REPORT_DIR = os.path.expanduser("~/projects/memory-glasses/hardware/cad/reports")
# Visual helpers that are meant to pass through other parts.
IGNORE = ("Light path", "Right eye", "coating", "cavity")
MIN_OVERLAP_MM3 = 0.5
# Imported vendor models: their own sub-parts touch by design, so only check them against our parts.
VENDOR_MARKERS = ("XIAO-ESP32S3-Sense",)

app = adsk.core.Application.get()


def mm_box(body):
    b = body.boundingBox
    lo, hi = b.minPoint, b.maxPoint
    return "x {:7.1f}..{:7.1f}  y {:7.1f}..{:7.1f}  z {:7.1f}..{:7.1f}  ({:.1f} x {:.1f} x {:.1f})".format(
        lo.x * 10, hi.x * 10, lo.y * 10, hi.y * 10, lo.z * 10, hi.z * 10,
        (hi.x - lo.x) * 10, (hi.y - lo.y) * 10, (hi.z - lo.z) * 10,
    )


def all_bodies(design):
    out = []
    root = design.rootComponent
    for b in root.bRepBodies:
        out.append((root.name, b, False))
    for occ in root.allOccurrences:
        vendor = any(m in occ.fullPathName for m in VENDOR_MARKERS)
        for b in occ.component.bRepBodies:
            out.append((occ.component.name, b.createForAssemblyContext(occ), vendor))
    return out


def run(context):
    ui = app.userInterface
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        bodies = all_bodies(design)
        lines = ["BODIES ({})".format(len(bodies))]
        for comp, b, _vendor in bodies:
            lines.append("{:38s} {:42s} vol {:9.1f} mm3  {}".format(
                comp[:38], b.name[:42], b.volume * 1000, mm_box(b)))

        solids = [(c, b, v) for c, b, v in bodies if not any(k in b.name for k in IGNORE)]
        lines.append("")
        lines.append("OVERLAPS > {} mm3".format(MIN_OVERLAP_MM3))
        tbm = adsk.fusion.TemporaryBRepManager.get()
        found = 0
        unchecked = []
        for (ca, a, va), (cb, b, vb) in itertools.combinations(solids, 2):
            if va and vb:
                continue
            ba, bb = a.boundingBox, b.boundingBox
            if not ba.intersects(bb):
                continue
            ta, tb = tbm.copy(a), tbm.copy(b)
            try:
                tbm.booleanOperation(ta, tb, adsk.fusion.BooleanTypes.IntersectionBooleanType)
            except RuntimeError:
                # Fusion can't intersect some pairs whose faces exactly coincide; list them for a manual look
                unchecked.append("{} / {}  <->  {} / {}".format(ca, a.name, cb, b.name))
                continue
            vol = ta.volume * 1000
            if vol > MIN_OVERLAP_MM3:
                found += 1
                lines.append("{:8.1f} mm3  {} / {}  <->  {} / {}".format(vol, ca, a.name, cb, b.name))
        if not found:
            lines.append("none")
        if unchecked:
            lines.append("")
            lines.append("COULD NOT COMPARE (touching faces confused Fusion's intersect)")
            lines.extend(unchecked)
        # v2 builds tag themselves so their report doesn't overwrite v1's.
        tag = design.attributes.itemByName("MemoryGlasses", "version")
        name = "check-report-v{}.txt".format(tag.value) if tag else "check-report.txt"
        with open(os.path.join(REPORT_DIR, name), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        ui.messageBox("Check written: {} bodies, {} overlaps, {} pairs not comparable.".format(len(bodies), found, len(unchecked)))
    except Exception:
        ui.messageBox("MemoryGlassesCheck failed:\n{}".format(traceback.format_exc()))
