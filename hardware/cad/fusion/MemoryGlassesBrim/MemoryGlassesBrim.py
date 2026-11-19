"""Builds the Memory Glasses brim-hat concept: a soft strap, and one rigid print of the brow band and a visor brim
with the camera at its front edge and the display's light path hidden underneath.

The OLED lies flat under the brim, facing down onto a mirror that sends its light forward along the brim's
underside to a second mirror at the brim's edge, down a short post into the beam-splitter cube, and back to the
right eye. The path is 201mm, so the eye focuses on the display as if it were 8 inches away.
Three reflections leave the picture mirrored, which the glasses' Mirror setting undoes.

The camera looks forward from the brim's front edge; the script draws its view cone and reports how far the pod
sits off its axis. A general concept, not a print-ready part.
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

B = {
    "band_z": (22.0, 48.0),
    "brim_z": (44.0, 48.0),
    "brim_reach": (105.0, 195.0),
    "brim_back_y": -20.0,
    "oled_y": 40.0,
    "drop": 7.0,
    "duct": (16.0, 16.0),
    "post_r": 8.0,
    "camera_half_fov": 28.0,
    "camera_view": 120.0,
    "aperture": (13.0, 9.0),
}


def light_path():
    """Return the light path from the OLED to the eye: OLED, lower mirror, edge mirror, cube, eye."""
    ex = H["eye_x"]
    under = B["brim_z"][0]
    oled_z = under - P["oled_t"] / 2
    turn_z = oled_z - B["drop"]
    tip = (H["view_distance"] - B["drop"] - turn_z + B["oled_y"]) / 2

    return [(ex, B["oled_y"], oled_z), (ex, B["oled_y"], turn_z), (ex, tip, turn_z), (ex, tip, 0), (ex, 0, 0)]


def camera_point():
    """Return where the camera lens sits: centred at the brim's front edge, just under it."""
    front = H["head_centre_y"] + B["brim_reach"][1]

    return (P["cam_across"], front - 2, B["brim_z"][0] - P["xiao_w"] / 2 - 2)


def rounded(comp, shape, radius):
    """Add a temporary body as a solid and round all its edges."""
    body = solid(comp, "piece", shape)
    v1.fillet_all(comp, body, radius)

    return body


def build_print(root, looks):
    """One rigid print: the brow band, the brim, the duct and post under it, the pod and the camera housing."""
    comp = v1.new_component(root, "Brim hat (one print, PETG)")
    ax, ay = H["head_half"]
    t = H["brow_t"]
    z0, z1 = B["band_z"]
    ex = H["eye_x"]
    oled, turn, edge, cube_at, _ = light_path()
    tip = edge[1]
    c = H["cube"]
    dw, dh = B["duct"]
    under = B["brim_z"][0]

    band = h1.ellipse_ring(comp, "Brim hat", z0, z1, (ax, ay), (ax + t, ay + t), looks["frame"])
    brim = h1.ellipse_ring(comp, "brim", B["brim_z"][0], B["brim_z"][1], (ax, ay), B["brim_reach"], looks["frame"])
    h1.combine(comp, band, [brim], adsk.fusion.FeatureOperations.JoinFeatureOperation)
    keep = solid(comp, "keep", box_span(-200, 200, B["brim_back_y"], 200, z0 - 1, z1 + 1))
    h1.combine(comp, band, [keep], adsk.fusion.FeatureOperations.IntersectFeatureOperation)

    cam = camera_point()
    pieces = [
        rounded(comp, box_span(ex - dw / 2, ex + dw / 2, B["oled_y"] - P["oled_pcb"] / 2 - 3, tip + dw / 2,
                               under - dh, under + 1), 6.5),
        rounded(comp, box(ex, tip, 0, c + 6, c + 6, c + 6), 8.0),
        rounded(comp, box_span(-P["xiao_len"] / 2 - 4, P["xiao_len"] / 2 + 4, cam[1] - P["xiao_stack"] - 4,
                               cam[1] + 2, under - P["xiao_w"] - 5, under + 1), 6.0),
        solid(comp, "post", cyl((ex, tip, turn[2]), (ex, tip, c / 2), B["post_r"])),
    ]
    h1.combine(comp, band, pieces, adsk.fusion.FeatureOperations.JoinFeatureOperation)
    v1.fillet_all(comp, band, 2.0)

    aw, ah = B["aperture"]
    oled_size = P["oled_pcb"] + 2 * P["clear"]
    voids = [
        box_span(ex - oled_size / 2, ex + oled_size / 2, B["oled_y"] - oled_size / 2, B["oled_y"] + oled_size / 2,
                 under - P["oled_t"] - P["clear"], under),
        box_span(ex - aw / 2, ex + aw / 2, B["oled_y"] - aw / 2, tip + ah / 2, turn[2] - ah / 2, under - 1),
        box_span(ex - aw / 2, ex + aw / 2, tip - ah / 2, tip + ah / 2, 0, turn[2] + ah / 2),
        box(ex, tip, 0, c + 2 * P["clear"], c + 2 * P["clear"], c + 2 * P["clear"]),
        box(ex, tip, 0, H["window"], 3 * c, H["window"]),
        box_span(-P["xiao_len"] / 2 - P["clear"], P["xiao_len"] / 2 + P["clear"], cam[1] - P["xiao_stack"],
                 cam[1] - P["clear"], under - P["xiao_w"] - P["clear"], under),
        cyl((cam[0], cam[1] - 4, cam[2]), (cam[0], cam[1] + 10, cam[2]), H["cam_hole_r"]),
    ]
    cut_from(comp, "Brim hat", voids)

    slot_w, slot_h = H["strap_slot"]
    ends = []

    for side in (-1, 1):
        y = B["brim_back_y"] + 6
        ends.append(box(side * (h1.brow_x(y) - t / 2), y, (z0 + z1) / 2 - 3, 4 * t, slot_w, slot_h - 4))

    cut_from(comp, "Brim hat", ends)


def plate(centre, normal, width, height):
    """A thin rectangular mirror plate at centre, facing along normal."""
    n = adsk.core.Vector3D.create(*normal)
    n.normalize()
    across = adsk.core.Vector3D.create(1, 0, 0)
    up = n.crossProduct(across)
    up.normalize()
    obb = adsk.core.OrientedBoundingBox3D.create(pt(*centre), across, up, v1.cm(width), v1.cm(height), v1.cm(1))

    return tbm.createBox(obb)


def build_reference(root, looks):
    """Parts that aren't printed, the light path, the eyes and the camera's view cone."""
    comp = v1.new_component(root, "Reference (not printed)")
    oled, turn, edge, cube_at, eye = light_path()
    c = H["cube"]
    cam = camera_point()

    solid(comp, "Beam-splitter cube", box(*cube_at, c, c, c), looks["glass"], 0.5)
    solid(comp, "OLED, facing down", box(*oled, P["oled_pcb"], P["oled_pcb"], P["oled_t"]), looks["pcb"])
    solid(comp, "Mirror under the OLED", plate(turn, (0, 1, 1), 16, 14), looks["mirror"])
    solid(comp, "Mirror at the brim's edge", plate(edge, (0, -1, 1), 16, 14), looks["mirror"])
    solid(comp, "XIAO ESP32-S3 Sense", box(0, cam[1] - P["xiao_stack"] / 2, cam[2], P["xiao_len"], P["xiao_stack"],
                                           P["xiao_w"]), looks["pcb"])

    for a, b in zip([oled, turn, edge, cube_at], [turn, edge, cube_at, eye]):
        solid(comp, "Light path", cyl(a, b, 0.5), looks["ray"], 0.7)

    for side in (-1, 1):
        solid(comp, f"{'Right' if side > 0 else 'Left'} eye",
              tbm.createSphere(pt(side * H["eye_x"], -12, 0), v1.cm(12)), looks["eye"])

    reach = B["camera_view"]
    spread = reach * math.tan(math.radians(B["camera_half_fov"]))
    cone = tbm.createCylinderOrCone(pt(*cam), v1.cm(0.01), pt(cam[0], cam[1] + reach, cam[2]), v1.cm(spread))
    solid(comp, "Camera view", cone, looks["view"], 0.15)


def pod_angle():
    """Return how far the nearest point of the post and pod sits off the camera's axis, in degrees."""
    cam = camera_point()
    tip = light_path()[2][1]
    near_x = H["eye_x"] - (H["cube"] + 6) / 2
    ahead = max(tip + (H["cube"] + 6) / 2 - cam[1], 0.0)

    return math.degrees(math.atan2(near_x - cam[0], ahead))


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
        camera.eye = pt(260, 260, 120)
        camera.target = pt(0, 40, 20)
        camera.upVector = adsk.core.Vector3D.create(0, 0, 1)
        camera.isFitView = True
        viewport.camera = camera

        angle = pod_angle()
        clear = "outside" if angle > B["camera_half_fov"] else "INSIDE"
        ui.messageBox(f"The cube sits {light_path()[2][1]:.0f}mm in front of the eye; the light path is "
                      f"{H['view_distance']:.0f}mm. The pod is {angle:.0f} degrees off the camera's axis, {clear} "
                      f"its {B['camera_half_fov']:.0f} degree half view.")
    except Exception:
        ui.messageBox("Brim hat build failed:\n" + traceback.format_exc())
