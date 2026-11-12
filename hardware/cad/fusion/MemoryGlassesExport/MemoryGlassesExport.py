"""Exports every printed part of the open Memory Glasses v2 file as a raw STL in model position.

hardware/cad/print/prepare_v2_stls.py then lays each one out for printing, checks it and writes hardware/cad/print/v2/.
Only touches disk: the design isn't changed, so the file stays saved.
"""

import os
import re
import traceback

import adsk.core
import adsk.fusion

RAW_DIR = os.path.expanduser("~/projects/memory-glasses/hardware/cad/print/v2/_raw")
# (component, exact body name, raw file name). Paired spacers and the six lens tabs are identical, so one of
# each shape is exported.
FRAME, CAM, POD = "Frame (print)", "Left hinge block: camera + XIAO", "Optics pod (HUD)"
PARTS = [
    (FRAME, "Front unit: frame + camera housing + pod housing (print)", "front-unit"),
    (FRAME, "Left temple", "left-arm"),
    (FRAME, "Right temple (battery inside)", "right-arm"),
    (FRAME, "Battery sliding lid (print)", "battery-lid"),
    (FRAME, "Wire cover strip (print)", "wire-cover-strip"),
    (FRAME, "Lens retainer tab R1 (print)", "tab"),
    (CAM, "Camera block sliding cap (print)", "camera-cap"),
    (POD, "Optics tunnel sliding lid (print)", "tunnel-lid"),
    (POD, "Optics cube + lens cap (print)", "cube-lens-cap"),
    (POD, "Display sled (print)", "display-sled"),
    (POD, "Focus spacer (front, print)", "spacer-front"),
    (POD, "Focus spacer (rear, print)", "spacer-rear"),
    (POD, "Tinted pod window (print dark or cut acrylic)", "tinted-window"),
]

app = adsk.core.Application.get()


def run(context):
    ui = app.userInterface
    try:
        doc = app.activeDocument
        if doc is None or not re.fullmatch(r"Memory Glasses v2( v\d+)?", doc.name):
            ui.messageBox("Open the 'Memory Glasses v2' tab first, then run this again.")
            return
        design = adsk.fusion.Design.cast(app.activeProduct)
        os.makedirs(RAW_DIR, exist_ok=True)
        missing = []
        for comp_name, body_name, file_name in PARTS:
            comp = design.allComponents.itemByName(comp_name)
            body = comp.bRepBodies.itemByName(body_name) if comp else None
            if body is None:
                missing.append(body_name)
                continue
            opts = design.exportManager.createSTLExportOptions(body, os.path.join(RAW_DIR, file_name + ".stl"))
            opts.isBinaryFormat = True
            opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
            design.exportManager.execute(opts)
        text = "Exported {} of {} parts.".format(len(PARTS) - len(missing), len(PARTS))
        if missing:
            text += "\nMissing:\n" + "\n".join(missing)
        ui.messageBox(text)
    except Exception:
        ui.messageBox("MemoryGlassesExport failed:\n{}".format(traceback.format_exc()))
