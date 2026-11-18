"""Builds the Memory Glasses headset concept: a soft head strap, and one rigid print holding the camera at the brow
and a boom that puts the display and cube about 8 inches (203mm) in front of the right eye.

This is a general concept to judge size and layout, not a print-ready part. Every size is in H at the top.
Axes as in v1: +X wearer's right, +Y forward, +Z up (mm). The right eye's cornea is at (eye_x, 0, 0).
"""

import importlib
import os
import sys
import traceback

import adsk.core
import adsk.fusion

V1_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "MemoryGlasses")
if V1_DIR not in sys.path:
    sys.path.insert(0, V1_DIR)
import MemoryGlasses as v1

importlib.reload(v1)
P = v1.P
box, box_span, cyl, solid, cut_from = v1.box, v1.box_span, v1.cyl, v1.solid, v1.cut_from
pt, val, tbm, app = v1.pt, v1.val, v1.tbm, v1.app

H = {
    "eye_x": 32.0,
    "view_distance": 203.0,
    "head_centre_y": -85.0,
    "head_half": (80.0, 100.0),
    "brow_t": 4.0,
    "brow_z": (22.0, 54.0),
    "brow_back_y": -30.0,
    "strap_t": 3.0,
    "strap_z": (26.0, 50.0),
    "strap_slot": (6.0, 26.0),
    "xiao_box": (24.0, 19.0, 30.0),
    "cam_hole_r": 4.5,
    "boom_w": 10.0,
    "boom_h": 10.0,
    "boom_wall": 2.5,
    "cube": P["cube"],
    "cage_wall": 3.0,
    "window": 13.0,
    "oled_housing": (31.0, 31.0, 8.0),
}


def brow_x(y):
    """Return how far out the outside of the brow band is, to the right, at a given y."""
    ax, ay = H["head_half"][0] + H["brow_t"], H["head_half"][1] + H["brow_t"]

    return ax * (1 - ((y - H["head_centre_y"]) / ay) ** 2) ** 0.5


def brow_front_y(x):
    """Return where the outside of the brow band is at a given x, on its front arc."""
    ax, ay = H["head_half"][0] + H["brow_t"], H["head_half"][1] + H["brow_t"]

    return H["head_centre_y"] + ay * (1 - (x / ax) ** 2) ** 0.5


def ellipse_ring(comp, name, z0, z1, inner, outer, look):
    """Extrude the ring between two ellipses centred on the head, from z0 to z1."""
    plane_input = comp.constructionPlanes.createInput()
    plane_input.setByOffset(comp.xYConstructionPlane, val(z0))
    plane = comp.constructionPlanes.add(plane_input)
    plane.isLightBulbOn = False
    sketch = comp.sketches.add(plane)
    sketch.isVisible = False

    for half_x, half_y in (inner, outer):
        centre = sketch.modelToSketchSpace(pt(0, H["head_centre_y"], z0))
        major = sketch.modelToSketchSpace(pt(0, H["head_centre_y"] + half_y, z0))
        minor = sketch.modelToSketchSpace(pt(half_x, H["head_centre_y"], z0))
        sketch.sketchCurves.sketchEllipses.add(centre, major, minor)

    profile = max((sketch.profiles.item(i) for i in range(sketch.profiles.count)),
                  key=lambda p: p.profileLoops.count)
    extrude = comp.features.extrudeFeatures.addSimple(
        profile, val(z1 - z0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    body = extrude.bodies.item(0)
    body.name = name
    v1.style(body, look)

    return body


def side_profile(comp, name, x, half_width, points, operation, target=None):
    """Extrude a closed (y, z) outline drawn at x, half_width to each side."""
    plane_input = comp.constructionPlanes.createInput()
    plane_input.setByOffset(comp.yZConstructionPlane, val(x))
    plane = comp.constructionPlanes.add(plane_input)
    plane.isLightBulbOn = False
    sketch = comp.sketches.add(plane)
    sketch.isVisible = False

    corners = [sketch.modelToSketchSpace(pt(x, y, z)) for y, z in points]
    lines = sketch.sketchCurves.sketchLines

    for a, b in zip(corners, corners[1:] + corners[:1]):
        lines.addByTwoPoints(a, b)

    extrudes = comp.features.extrudeFeatures
    extrude_input = extrudes.createInput(sketch.profiles.item(0), operation)
    extrude_input.setSymmetricExtent(val(half_width), True)

    if target is not None:
        extrude_input.participantBodies = [target]

    extrude = extrudes.add(extrude_input)

    if operation == adsk.fusion.FeatureOperations.NewBodyFeatureOperation:
        extrude.bodies.item(0).name = name

    return extrude


def boom_outline(inset):
    """Side outline of the boom (y, z): from inside the brow, forward and down to the top of the display housing."""
    top = [(6, 52), (60, 50), (120, 45), (170, 33), (H["view_distance"] - 8, 23)]
    bottom = [(H["view_distance"] - 8, 23 - H["boom_h"]), (170, 23), (120, 35), (60, 40), (6, 42)]

    if inset:
        top = [(y + (inset if i == 0 else 0), z - inset) for i, (y, z) in enumerate(top)]
        bottom = [(y + (inset if i == len(bottom) - 1 else 0), z + inset) for i, (y, z) in enumerate(bottom)]

    return top + bottom


def combine(comp, target, tools, operation):
    """Join or cut bodies into a target body."""
    collection = adsk.core.ObjectCollection.create()

    for tool in tools:
        collection.add(tool)

    combine_input = comp.features.combineFeatures.createInput(target, collection)
    combine_input.operation = operation
    comp.features.combineFeatures.add(combine_input)


def build_print(root, looks):
    """The one rigid print: front of the brow band, electronics housing, boom and optics pod, joined."""
    comp = v1.new_component(root, "Headset (one print, PETG)")
    join = adsk.fusion.FeatureOperations.JoinFeatureOperation
    cut = adsk.fusion.FeatureOperations.CutFeatureOperation
    ax, ay = H["head_half"]
    t = H["brow_t"]
    z0, z1 = H["brow_z"]

    brow = ellipse_ring(comp, "Headset", z0, z1, (ax, ay), (ax + t, ay + t), looks["frame"])
    keep = solid(comp, "keep", box_span(-150, 150, H["brow_back_y"], 40, z0 - 1, z1 + 1))
    combine(comp, brow, [keep], adsk.fusion.FeatureOperations.IntersectFeatureOperation)

    sx, sz = H["xiao_box"][0], H["xiao_box"][2]
    front = brow_front_y(0)
    housing = solid(comp, "housing", box_span(-sx / 2, sx / 2, front - 4, front + H["xiao_box"][1],
                                              z0 + 1, z0 + 1 + sz))
    combine(comp, brow, [housing], join)

    boom = side_profile(comp, "boom", H["eye_x"], H["boom_w"] / 2, boom_outline(0),
                        adsk.fusion.FeatureOperations.NewBodyFeatureOperation).bodies.item(0)
    combine(comp, brow, [boom], join)

    c, w = H["cube"], H["cage_wall"]
    ox, oy, oz = H["oled_housing"]
    cx, cy = H["eye_x"], H["view_distance"]
    cage = solid(comp, "cage", box(cx, cy, 0, c + 2 * w, c + 2 * w, c + 2 * w))
    lid = solid(comp, "display housing", box_span(cx - ox / 2, cx + ox / 2, cy - oy / 2, cy + oy / 2,
                                                  c / 2 + w - 1, c / 2 + w - 1 + oz))
    combine(comp, brow, [cage, lid], join)
    v1.fillet_all(comp, brow, 1.5)

    oled = P["oled_pcb"] + 2 * P["clear"]
    voids = [
        box(cx, cy, 0, c + 2 * P["clear"], c + 2 * P["clear"], c + 2 * P["clear"]),
        box(cx, cy, 0, H["window"], 3 * c, H["window"]),
        box_span(cx - oled / 2, cx + ox, cy - oled / 2, cy + oled / 2,
                 c / 2 + w, c / 2 + w + P["oled_t"] + P["clear"]),
        box(cx, cy, c / 2 + w / 2, P["oled_window"][0], P["oled_window"][1], w + 1),
        box_span(-P["xiao_w"] / 2 - P["clear"], P["xiao_w"] / 2 + P["clear"], front - 4,
                 front - 4 + P["xiao_stack"] + P["clear"], z0 + 3, z0 + 3 + P["xiao_len"] + 6),
        cyl((P["cam_across"], front, z0 + 3 + P["xiao_len"] / 2 + P["cam_along"]),
            (P["cam_across"], front + H["xiao_box"][1] + 1, z0 + 3 + P["xiao_len"] / 2 + P["cam_along"]),
            H["cam_hole_r"]),
    ]
    cut_from(comp, "Headset", voids)

    side_profile(comp, "boom channel", H["eye_x"], H["boom_w"] / 2 - H["boom_wall"],
                 boom_outline(H["boom_wall"]), cut, target=brow)

    slot_w, slot_h = H["strap_slot"]
    ends = []

    for side in (-1, 1):
        y = H["brow_back_y"] + 8
        ends.append(box(side * (brow_x(y) - t / 2), y, (z0 + z1) / 2, 4 * t, slot_w, slot_h))

    cut_from(comp, "Headset", ends)

    return comp


def build_strap(root, looks):
    """Soft strap: a full ring inside the brow; its ends thread through the slots at the back of the brow."""
    comp = v1.new_component(root, "Head strap (TPU or silicone)")
    ax, ay = H["head_half"]
    s = H["strap_t"]
    ellipse_ring(comp, "Head strap", H["strap_z"][0], H["strap_z"][1], (ax - s, ay - s), (ax, ay), looks["strap"])


def build_reference(root, looks):
    """Parts that aren't printed, plus eyes and the line of sight, to judge the layout."""
    comp = v1.new_component(root, "Reference (not printed)")
    c, w = H["cube"], H["cage_wall"]
    cx, cy = H["eye_x"], H["view_distance"]
    z0 = H["brow_z"][0]
    front = brow_front_y(0)

    solid(comp, "Beam-splitter cube", box(cx, cy, 0, c, c, c), looks["glass"], 0.5)
    solid(comp, "OLED", box(cx, cy, c / 2 + w + P["oled_t"] / 2, P["oled_pcb"], P["oled_pcb"], P["oled_t"]),
          looks["pcb"])
    solid(comp, "XIAO ESP32-S3 Sense", box(0, front - 4 + P["xiao_stack"] / 2, z0 + 3 + P["xiao_len"] / 2,
                                           P["xiao_w"], P["xiao_stack"], P["xiao_len"]), looks["pcb"])

    for side in (-1, 1):
        solid(comp, f"{'Right' if side > 0 else 'Left'} eye",
              tbm.createSphere(pt(side * H["eye_x"], -12, 0), v1.cm(12)), looks["eye"])

    solid(comp, "Line of sight, 203mm", cyl((cx, 0, 0), (cx, cy - c / 2, 0), 0.6), looks["ray"], 0.6)


def run(context):
    ui = app.userInterface

    try:
        for doc in [app.documents.item(i) for i in range(app.documents.count)]:
            if doc.dataFile is None and doc.name.startswith("Untitled"):
                doc.close(False)

        app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(app.activeProduct)
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = design.rootComponent

        find = v1.find_appearance
        looks = {
            "frame": find("plastic", "glossy", "black") or find("plastic", "black"),
            "strap": find("plastic", "matte", "blue") or find("plastic", "blue") or find("blue"),
            "glass": find("glass"),
            "pcb": find("plastic", "green") or find("green"),
            "eye": find("plastic", "white"),
            "ray": find("red"),
        }

        build_print(root, looks)
        build_strap(root, looks)
        build_reference(root, looks)

        viewport = app.activeViewport
        camera = viewport.camera
        camera.eye = pt(260, 120, 160)
        camera.target = pt(0, 60, 10)
        camera.upVector = adsk.core.Vector3D.create(0, 0, 1)
        camera.isFitView = True
        viewport.camera = camera
    except Exception:
        ui.messageBox("Headset build failed:\n" + traceback.format_exc())
