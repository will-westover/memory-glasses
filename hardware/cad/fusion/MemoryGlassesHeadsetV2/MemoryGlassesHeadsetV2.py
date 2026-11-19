"""Builds the Memory Glasses headset concept v2: folded optics on a short boom, styled as one sleek brow module.

Light leaves the OLED, which faces sideways in the brow module, runs along the brow to a mirror, forward to a
second mirror at the boom tip, down into the beam-splitter cube, and back to the right eye. That path is 201mm
long, so the eye focuses on the display as if it were 8 inches away while the boom reaches only 70mm forward.
Three reflections leave the picture mirrored, which the glasses' Mirror setting undoes.

The camera sits above the left eye, away from the boom, so the pod stays out of its view; the script draws the
camera's view cone and reports the angle to the pod. A general concept, not a print-ready part.
Axes as in v1: +X wearer's right, +Y forward, +Z up (mm). The right eye's cornea is at (eye_x, 0, 0).
"""

import importlib
import math
import os
import sys
import traceback

import adsk.core
import adsk.fusion

HERE = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

for folder in ("MemoryGlasses", "MemoryGlassesHeadset"):
    if os.path.join(HERE, folder) not in sys.path:
        sys.path.insert(0, os.path.join(HERE, folder))

import MemoryGlasses as v1
import MemoryGlassesHeadset as h1

importlib.reload(v1)
importlib.reload(h1)
P, H = v1.P, h1.H
box, box_span, cyl, solid, cut_from = v1.box, v1.box_span, v1.cyl, v1.solid, v1.cut_from
pt, tbm, app = v1.pt, v1.tbm, v1.app

F = {
    "cube_y": 70.0,
    "fold_z": 50.0,
    "brow_leg_y": 29.0,
    "camera_x": -32.0,
    "camera_z": 34.0,
    "camera_half_fov": 28.0,
    "camera_view": 120.0,
    "aperture": (13.0, 9.0),
    "boom_r": 9.0,
    "module_fillet": 3.0,
}


def path_points():
    """Return the light path from the OLED to the eye: OLED, brow mirror, tip mirror, cube, eye."""
    total = H["view_distance"]
    tip, fold, brow = F["cube_y"], F["fold_z"], F["brow_leg_y"]
    oled_x = H["eye_x"] - (total - tip - fold - (tip - brow))

    return [(oled_x, brow, fold), (H["eye_x"], brow, fold), (H["eye_x"], tip, fold), (H["eye_x"], tip, 0),
            (H["eye_x"], 0, 0)]


def plate(centre, normal, width, height, thickness=1.0):
    """A thin rectangular mirror plate at centre, facing along normal."""
    n = adsk.core.Vector3D.create(*normal)
    n.normalize()
    up = adsk.core.Vector3D.create(0, 0, 1) if abs(normal[2]) < 0.5 else adsk.core.Vector3D.create(1, 0, 0)
    across = n.crossProduct(up)
    across.normalize()
    up = across.crossProduct(n)
    up.normalize()
    obb = adsk.core.OrientedBoundingBox3D.create(pt(*centre), across, up, v1.cm(width), v1.cm(height),
                                                 v1.cm(thickness))

    return tbm.createBox(obb)


def union(bodies):
    """Join temporary bodies into one."""
    first = bodies[0]

    for other in bodies[1:]:
        tbm.booleanOperation(first, other, adsk.fusion.BooleanTypes.UnionBooleanType)

    return first


def build_print(root, looks):
    """One rigid print: the brow band's front, the brow module with camera and folded optics, boom and pod."""
    comp = v1.new_component(root, "Headset v2 (one print, PETG)")
    ax, ay = H["head_half"]
    t = H["brow_t"]
    z0, z1 = H["brow_z"]
    oled, brow_mirror, tip_mirror, cube_at, _ = path_points()
    ex, tip, fold, brow = H["eye_x"], F["cube_y"], F["fold_z"], F["brow_leg_y"]
    c = H["cube"]

    band = h1.ellipse_ring(comp, "Headset v2", z0, z1, (ax, ay), (ax + t, ay + t), looks["frame"])
    keep = solid(comp, "keep", box_span(-150, 150, H["brow_back_y"], 60, z0 - 1, z1 + 1))
    h1.combine(comp, band, [keep], adsk.fusion.FeatureOperations.IntersectFeatureOperation)

    cam_front = h1.brow_front_y(F["camera_x"])
    xiao_z = F["camera_z"] - P["xiao_len"] / 2 - P["cam_along"]
    r = F["boom_r"]
    blocks = [
        (box_span(oled[0] - 8, ex + r, brow - 14, brow + 14, fold - 15, fold + 15), 11.0),
        (box_span(F["camera_x"] - 13, oled[0] + 2, cam_front - 4, cam_front + P["xiao_stack"] + 4,
                  xiao_z - 2, fold + 8), 9.0),
        (box(ex, tip, 0, c + 6, c + 6, c + 6), 8.0),
    ]
    pieces = []

    for shape, radius in blocks:
        piece = solid(comp, "piece", shape)
        v1.fillet_all(comp, piece, radius)
        pieces.append(piece)

    tube = union([cyl((ex, brow, fold), (ex, tip, fold), r), tbm.createSphere(pt(ex, tip, fold), v1.cm(r)),
                  cyl((ex, tip, fold), (ex, tip, c / 2), r)])
    pieces.append(solid(comp, "boom", tube))
    h1.combine(comp, band, pieces, adsk.fusion.FeatureOperations.JoinFeatureOperation)
    v1.fillet_all(comp, band, F["module_fillet"])

    aw, ah = F["aperture"]
    oled_size = P["oled_pcb"] + 2 * P["clear"]
    voids = [
        box_span(oled[0], ex + aw / 2, brow - aw / 2 - 3, brow + aw / 2 + 3, fold - ah / 2 - 2, fold + ah / 2 + 2),
        box_span(ex - aw / 2, ex + aw / 2, brow - aw / 2, tip + ah / 2, fold - ah / 2, fold + ah / 2),
        box_span(ex - aw / 2, ex + aw / 2, tip - ah / 2, tip + ah / 2, 0, fold + ah / 2),
        box(ex, tip, 0, c + 2 * P["clear"], c + 2 * P["clear"], c + 2 * P["clear"]),
        box(ex, tip, 0, H["window"], 3 * c, H["window"]),
        box_span(oled[0] - P["oled_t"] - P["clear"], oled[0], brow - oled_size / 2, brow + oled_size / 2,
                 fold - oled_size / 2, fold + oled_size / 2 + 20),
        box_span(F["camera_x"] - P["xiao_w"] / 2 - P["clear"], F["camera_x"] + P["xiao_w"] / 2 + P["clear"],
                 cam_front - 2, cam_front - 2 + P["xiao_stack"] + P["clear"], xiao_z, fold + 20),
        cyl((F["camera_x"] + P["cam_across"], cam_front, F["camera_z"]),
            (F["camera_x"] + P["cam_across"], cam_front + P["xiao_stack"] + 10, F["camera_z"]), H["cam_hole_r"]),
    ]
    cut_from(comp, "Headset v2", voids)

    slot_w, slot_h = H["strap_slot"]
    ends = []

    for side in (-1, 1):
        y = H["brow_back_y"] + 8
        ends.append(box(side * (h1.brow_x(y) - t / 2), y, (z0 + z1) / 2, 4 * t, slot_w, slot_h))

    cut_from(comp, "Headset v2", ends)

    return comp


def build_reference(root, looks):
    """Parts that aren't printed, the light path, the eyes and the camera's view cone."""
    comp = v1.new_component(root, "Reference (not printed)")
    oled, brow_mirror, tip_mirror, cube_at, eye = path_points()
    c = H["cube"]
    cam_front = h1.brow_front_y(F["camera_x"])

    solid(comp, "Beam-splitter cube", box(H["eye_x"], F["cube_y"], 0, c, c, c), looks["glass"], 0.5)
    solid(comp, "OLED, facing along the brow",
          box(oled[0] - P["oled_t"] / 2, F["brow_leg_y"], F["fold_z"], P["oled_t"], P["oled_pcb"], P["oled_pcb"]),
          looks["pcb"])
    solid(comp, "Brow mirror", plate(brow_mirror, (1, -1, 0), 18, 14), looks["mirror"])
    solid(comp, "Tip mirror", plate(tip_mirror, (0, 1, 1), 16, 14), looks["mirror"])
    solid(comp, "XIAO ESP32-S3 Sense",
          box(F["camera_x"], cam_front - 2 + P["xiao_stack"] / 2, F["camera_z"] - P["cam_along"],
              P["xiao_w"], P["xiao_stack"], P["xiao_len"]), looks["pcb"])

    for a, b in zip([oled, brow_mirror, tip_mirror, cube_at], [brow_mirror, tip_mirror, cube_at, eye]):
        solid(comp, "Light path", cyl(a, b, 0.5), looks["ray"], 0.7)

    for side in (-1, 1):
        solid(comp, f"{'Right' if side > 0 else 'Left'} eye",
              tbm.createSphere(pt(side * H["eye_x"], -12, 0), v1.cm(12)), looks["eye"])

    reach = F["camera_view"]
    spread = reach * math.tan(math.radians(F["camera_half_fov"]))
    apex = (F["camera_x"], cam_front + 2, F["camera_z"])
    cone = tbm.createCylinderOrCone(pt(*apex), v1.cm(0.01), pt(apex[0], apex[1] + reach, apex[2]), v1.cm(spread))
    solid(comp, "Camera view", cone, looks["view"], 0.15)


def pod_angle():
    """Return how far the pod's nearest edge sits off the camera's axis, in degrees."""
    cam_front = h1.brow_front_y(F["camera_x"])
    near_x = H["eye_x"] - (H["cube"] + 6) / 2

    return math.degrees(math.atan2(near_x - F["camera_x"], F["cube_y"] - cam_front))


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
            "frame": find("plastic", "matte", "black") or find("plastic", "black"),
            "strap": find("plastic", "matte", "gray") or find("plastic", "gray") or find("gray"),
            "glass": find("glass"),
            "pcb": find("plastic", "green") or find("green"),
            "mirror": find("mirror") or find("chrome"),
            "eye": find("plastic", "white"),
            "ray": find("red"),
            "view": find("plastic", "yellow") or find("yellow"),
        }

        build_print(root, looks)
        h1.build_strap(root, looks)
        build_reference(root, looks)

        viewport = app.activeViewport
        camera = viewport.camera
        camera.eye = pt(220, 200, 140)
        camera.target = pt(0, 30, 20)
        camera.upVector = adsk.core.Vector3D.create(0, 0, 1)
        camera.isFitView = True
        viewport.camera = camera

        angle = pod_angle()
        clear = "outside" if angle > F["camera_half_fov"] else "INSIDE"
        ui.messageBox(f"The pod sits {angle:.0f} degrees off the camera's axis, {clear} its "
                      f"{F['camera_half_fov']:.0f} degree half view.")
    except Exception:
        ui.messageBox("Headset v2 build failed:\n" + traceback.format_exc())
