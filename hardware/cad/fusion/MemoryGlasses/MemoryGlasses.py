"""Builds the memory-glasses assembly (Ray-Ban Meta Wayfarer-style printed frame) in a new Fusion document.

Axes (mm): +X = wearer's right, +Y = forward (away from face), +Z = up.
In P, "spec" values come from a datasheet or listing; "MEASURE" values are educated guesses to caliper
against the real part before printing anything that depends on them.
"""

import os
import traceback

import adsk.core
import adsk.fusion

P = {
    # frame
    "front_t": 7.0,
    "front_fillet": 1.5,
    "temple_x": 65.6,
    # 1.6 sliding lid (0.8 lip + 0.8 tongue) + slide gap + 6.4 cell slot + outer wall
    "temple_t": 9.6,
    "temple_fillet": 2.5,
    "lid_t": 1.6,
    "pocket_y0": 5.0,
    # sliding fits: lids ride on tongues in grooves behind a lip; arms hang on T-rails in T-slots.
    # slide_clear is the gap per side; the slide-fit test piece picks the right value for your printer.
    # BYU's fit-test print (2026-11-06): 0.15 was the tightest slot the rail still slid through.
    "slide_clear": 0.15,
    "slide_test": (0.15, 0.25, 0.35),
    "tongue": 0.8,
    "lip_x": 0.8,
    "rail_stem": (2.0, 1.5),
    "rail_flange": (4.0, 1.2),
    # (x, z bottom, z top) of each arm's T-rail; the right one stops below the wire passage into the arm
    "rail_right": (70.6, 11.5, 17.5),
    "rail_left": (-71.0, 8.0, 20.0),
    # camera block back wall thick enough to hold the left arm's T-slot
    "block_back_wall": 5.0,
    # brow wire cover: 0.8 thick strip overlapping the groove 0.4 each side, ending before the hinge areas
    "cover_t": 0.8,
    "cover_overlap": 0.4,
    "cover_end_x": 66.0,
    # MEASURE: eye position relative to the lens centre depends on your face
    "eye": (37.4, -18.0, 0.0),
    # front lens seats for ThunderClap Ray-Ban Meta 50mm lenses
    "lip": 1.2,
    "lip_inset": 1.0,
    "lens_clear": 0.2,
    # MEASURE: base 6 curve (~88mm radius) and 2mm centre thickness are typical, not from the listing
    "lens_base_r": 88.3,
    "lens_ct": 2.0,
    # electronics: XIAO ESP32-S3 Sense, measured from Seeed's official STEP model (spec). The SD slot and
    # USB-C overhang one end, so the envelope is 24.42 long, 17.80 wide, 14.00 deep.
    "xiao_step": "~/projects/memory-glasses/hardware/cad/vendor/xiao-esp32s3-sense/model/Seeed Studio XIAO-ESP32-S3-Sense.step",
    # envelope centre in the STEP's own coordinates (x long, y stack/camera-up, z width), mm
    "xiao_step_centre": (3.21, 6.74, -6.11),
    "xiao_len": 24.42,
    "xiao_w": 17.80,
    "xiao_stack": 14.00,
    # camera lens axis relative to the envelope centre: 5.54 toward the USB/SD end, 0.64 across (spec)
    "cam_along": 5.54,
    "cam_across": 0.64,
    "cam_win": 9.0,
    # USB-C + microSD access at the USB end: 11.8 wide x 8.5 deep from the back of the stack (spec)
    "port_w0": -6.69,
    "port_w1": 5.11,
    "port_depth": 8.5,
    # USB-C port centre sits 4.63 behind the stack centre (spec); cable plug head needs 13 x 7
    "usb_dy": -4.63,
    "usb_plug": (13.0, 7.0),
    "xiao_cz": 11.5,
    "cam_r": 3.0,
    # MEASURE: U.FL antenna ships in the box, size unpublished; it lies flat against the outer wall
    "ant_gap": 1.4,
    # SS-12D series drawing (C&K, spec): body 8.6 x 3.5 x 3.5, pins 2-2.5, 1.5 knob with 2mm travel
    "sw_l": 9.2,
    "sw_w": 4.0,
    "sw_h": 6.5,
    # joinery: 1.75mm filament dowels, superglued
    "hole_r": 0.95,
    "pin_r": 0.85,
    "hole_depth": 4.2,
    # Brow groove 1.6 tall (the lens seats sit just below it) x 3.0 deep; behind the 0.8 cover that leaves
    # 1.6 x 2.2 for six 30 AWG wires (~0.6mm each, 2 high x 3 deep). Holes are 2.4 across.
    "wire_h": 1.6,
    "wire_depth": 3.0,
    "wire_r": 1.2,
    # MEASURE: 601452 = 6 x 14 x 52 by its size code; the listing gives no drawing
    "bat_t": 6.0,
    "bat_l": 52.0,
    "bat_h": 14.0,
    "bat_cz": 14.2,
    # HiLetgo module 27.0 x 27.0 x 4.1 (spec); its active area isn't centred on the PCB, so the tunnel
    # leaves oled_shim each side to pack it until the image is centred
    "oled_pcb": 27.0,
    "oled_t": 4.1,
    "oled_shim": 2.0,
    # optics: 15mm cube (spec), 22mm lens 7mm tall (spec), focal length 27mm "for reference only"
    "cube": 15.0,
    "cube_dx": 14.0,
    "cube_dz": 11.0,
    "lens_d": 22.0,
    "lens_t": 7.0,
    "lens_f": 27.0,
    "oled_gap_ratio": 0.9,
    # the OLED's sled can sit anywhere in this range of focal lengths; spacer rods hold it at one step
    "focus_min": 0.70,
    "focus_max": 1.05,
    "focus_steps": (0.80, 0.85, 0.90, 0.95, 1.00),
    "sled_lip": 1.2,
    "spacer_rod": 3.0,
    # window in the sled lip over the OLED's 21.74 x 11.2 active area (spec), with margin for its offset
    "oled_window": (23.0, 13.0),
    # dowel test coupon hole diameters
    "coupon_holes": (1.80, 1.85, 1.90, 1.95, 2.00),
    "fit_test_dir": "~/projects/memory-glasses/hardware/cad/print/fit-test",
    # printed parts
    "wall": 1.6,
    "clear": 0.2,
    "pod_fillet": 3.0,
}

# Traced pixel-by-pixel from a head-on Ray-Ban Meta Wayfarer photo at 0.1204 mm/px (that scale makes the
# lens exactly 50mm wide, the real 50-22 size). Right half (x, z), top centre clockwise to the keyhole bridge.
FRONT_RIGHT = [
    (0, 17.5), (7.3, 18.5), (14.5, 20.5), (21.7, 22.2), (29.0, 23.4), (36.2, 24.2), (43.4, 24.6),
    (50.6, 24.6), (57.9, 24.1), (65.1, 23.4), (72.3, 22.2), (74.6, 21.4), (75.2, 19.5), (74.8, 14.7),
    (74.6, 12.3), (73.0, 9.9), (69.9, 7.5), (67.8, 2.6), (66.5, -2.2), (65.3, -7.0), (63.8, -11.8),
    (61.5, -16.6), (58.6, -19.8), (52.6, -23.8), (46.5, -24.1), (34.5, -24.2), (28.5, -23.8),
    (24.5, -22.2), (20.9, -20.2), (17.5, -17.8), (13.4, -13.0), (10.5, -8.2), (8.4, -3.4),
    (6.4, 1.4), (4.3, 6.3), (0, 7.0),
]
LENS_TRACE = [
    (13.4, 14.6), (20.0, 16.8), (28.5, 18.2), (40.5, 19.3), (52.6, 18.9), (59.8, 18.1), (61.9, 15.9),
    (62.2, 9.9), (62.1, 0.2), (61.6, -5.0), (59.8, -9.4), (57.0, -14.0), (53.0, -17.8), (47.0, -19.3),
    (40.5, -19.6), (28.5, -19.3), (24.5, -18.3), (16.4, -13.2), (14.4, -9.4), (12.9, 0.2), (12.6, 9.9),
]
# The photo shows only the visible opening; the lens itself is the published RW4006 size, 50 x 41 (spec),
# so scale the traced shape about its centre to that size.
LENS_SIZE = (50.0, 41.0)


def _scaled_lens(trace, size):
    xs, zs = [x for x, _ in trace], [z for _, z in trace]
    cx, cz = (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2
    kx, kz = size[0] / (max(xs) - min(xs)), size[1] / (max(zs) - min(zs))
    return [(cx + (x - cx) * kx, cz + (z - cz) * kz) for x, z in trace]


LENS_RIGHT = _scaled_lens(LENS_TRACE, LENS_SIZE)
# Wire groove centreline behind the brow, right half (x, z): midway between lens top and brow top,
# dropping into the solid bridge at the centre where the brow dips.
WIRE_PATH = [
    (0, 13.0), (8, 14.7), (14, 17.9), (21, 19.9), (28.5, 21.2), (40.5, 22.3), (52.6, 22.0),
    (60, 21.3), (66, 20.3), (73, 19.0),
]


def temple_points(y_start):
    """Side profile (y, z): tall straight section level with the brow, then a taper and a gentle ear drop.

    18.4 tall through the battery and switch pockets so the sliding lid's grooves keep 1.2mm walls.
    """
    return [
        (y_start, 23.4), (y_start - 3, 23.4), (-60, 23.4), (-78, 23.2), (-100, 21.0), (-122, 18.5), (-138, 13),
        (-150, 4), (-153, -1), (-150, -4.5), (-144, -2), (-132, 5), (-118, 9.5), (-100, 11.5),
        (-80, 5.0), (y_start - 8, 5.0), (y_start - 3, 5.0), (y_start, 5.0), (y_start, 15),
    ]

app = adsk.core.Application.get()
tbm = adsk.fusion.TemporaryBRepManager.get()


def cm(v):
    return v / 10.0


def pt(x, y, z):
    return adsk.core.Point3D.create(cm(x), cm(y), cm(z))


def val(mm):
    return adsk.core.ValueInput.createByReal(cm(mm))


def box_span(x0, x1, y0, y1, z0, z1):
    obb = adsk.core.OrientedBoundingBox3D.create(
        pt((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2),
        adsk.core.Vector3D.create(1, 0, 0),
        adsk.core.Vector3D.create(0, 1, 0),
        cm(abs(x1 - x0)), cm(abs(y1 - y0)), cm(abs(z1 - z0)),
    )
    return tbm.createBox(obb)


def box(cx, cy, cz, sx, sy, sz):
    return box_span(cx - sx / 2, cx + sx / 2, cy - sy / 2, cy + sy / 2, cz - sz / 2, cz + sz / 2)


def cyl(a, b, r):
    return tbm.createCylinderOrCone(pt(*a), cm(r), pt(*b), cm(r))


def find_appearance(*keywords):
    lib = app.materialLibraries.itemByName("Fusion Appearance Library")
    if not lib:
        return None
    for i in range(lib.appearances.count):
        a = lib.appearances.item(i)
        if all(k in a.name.lower() for k in keywords):
            return a
    return None


def style(body, look=None, opacity=None):
    # Appearance and opacity are cosmetic; an API quirk there shouldn't abort the build.
    try:
        if look:
            body.appearance = look
        if opacity is not None:
            body.opacity = opacity
    except Exception:
        pass


def new_component(root, name):
    occ = root.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    occ.component.name = name
    return occ.component


def fillet_all(comp, body, r):
    for radius in (r, r / 2):
        edges = adsk.core.ObjectCollection.create()
        for e in body.edges:
            edges.add(e)
        fi = comp.features.filletFeatures.createInput()
        try:
            fi.edgeSetInputs.addConstantRadiusEdgeSet(edges, val(radius), True)
        except AttributeError:
            fi.addConstantRadiusEdgeSet(edges, val(radius), True)
        try:
            comp.features.filletFeatures.add(fi)
            return
        except Exception:
            continue


def solid(comp, name, tbody, look=None, opacity=None):
    bf = comp.features.baseFeatures.add()
    bf.startEdit()
    comp.bRepBodies.add(tbody, bf)
    bf.finishEdit()
    body = bf.bodies.item(0)
    body.name = name
    style(body, look, opacity)
    return body


def rounded_shell(comp, name, outer, cavities, r, look=None, opacity=None):
    """Rounded printed housing: fillet the outer block first, then cut cavities so thin walls never get filleted."""
    bf = comp.features.baseFeatures.add()
    bf.startEdit()
    comp.bRepBodies.add(outer, bf)
    for c in cavities:
        comp.bRepBodies.add(c, bf)
    bf.finishEdit()
    bodies = sorted((bf.bodies.item(i) for i in range(bf.bodies.count)), key=lambda b: -b.volume)
    bodies[0].name = name
    for i, t in enumerate(bodies[1:]):
        t.name = "{} cavity {}".format(name, i)
    fillet_all(comp, comp.bRepBodies.itemByName(name), r)
    if cavities:
        tools = adsk.core.ObjectCollection.create()
        for i in range(len(cavities)):
            tools.add(comp.bRepBodies.itemByName("{} cavity {}".format(name, i)))
        ci = comp.features.combineFeatures.createInput(comp.bRepBodies.itemByName(name), tools)
        ci.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
        ci.isKeepToolBodies = False
        comp.features.combineFeatures.add(ci)
    # A cut that splits the shell leaves extra bodies named "<name> (1)"; style those too so a split is visible, not hidden.
    for i in range(comp.bRepBodies.count):
        b = comp.bRepBodies.item(i)
        if b.name.startswith(name):
            style(b, look, opacity)
    return comp.bRepBodies.itemByName(name)


def closed_spline(sketch, model_pts):
    col = adsk.core.ObjectCollection.create()
    for p in model_pts:
        col.add(sketch.modelToSketchSpace(pt(*p)))
    spline = sketch.sketchCurves.sketchFittedSplines.add(col)
    spline.isClosed = True
    return spline


def mirrored_loop(right_half):
    left = [(-x, z) for x, z in reversed(right_half) if x != 0]
    return right_half + left


def lens_centre():
    xs = [x for x, _ in LENS_RIGHT]
    zs = [z for _, z in LENS_RIGHT]
    return (min(xs) + max(xs)) / 2, (min(zs) + max(zs)) / 2


def min_radius(loop, cx, cz, steps=20):
    """Closest distance from (cx, cz) to a closed polyline, sampled along each edge."""
    best = float("inf")
    for (xa, za), (xb, zb) in zip(loop, loop[1:] + loop[:1]):
        for i in range(steps):
            t = i / steps
            x, z = xa + (xb - xa) * t, za + (zb - za) * t
            best = min(best, ((x - cx) ** 2 + (z - cz) ** 2) ** 0.5)
    return best


def offset_curve(sketch, spline, toward, dist):
    curves = adsk.core.ObjectCollection.create()
    curves.add(spline)
    return sketch.offset(curves, sketch.modelToSketchSpace(pt(*toward)), cm(dist))


def plane_at_y(comp, y):
    pi = comp.constructionPlanes.createInput()
    pi.setByOffset(comp.xZConstructionPlane, val(y))
    plane = comp.constructionPlanes.add(pi)
    # The XZ plane's normal may point -Y; flip the offset if the plane landed on the wrong side.
    if abs(plane.geometry.origin.y - cm(y)) > 1e-6:
        plane.deleteMe()
        pi = comp.constructionPlanes.createInput()
        pi.setByOffset(comp.xZConstructionPlane, val(-y))
        plane = comp.constructionPlanes.add(pi)
    plane.isLightBulbOn = False
    return plane


def all_profiles(sketch):
    profs = adsk.core.ObjectCollection.create()
    for p in sketch.profiles:
        profs.add(p)
    return profs


def build_front(comp, looks):
    """Front with a 1mm lip around each opening; ThunderClap lenses drop into seats from the back."""
    lcx, lcz = lens_centre()
    ff, fb = P["front_t"] / 2, -P["front_t"] / 2

    sk = comp.sketches.add(comp.xZConstructionPlane)
    sk.name = "Front outline"
    closed_spline(sk, [(x, 0, z) for x, z in mirrored_loop(FRONT_RIGHT)])
    for s in (-1, 1):
        lens = closed_spline(sk, [(s * x, 0, z) for x, z in LENS_RIGHT])
        offset_curve(sk, lens, (s * lcx, 0, lcz), P["lip_inset"])
    # Regions between outline and lens loops plus the lip rings: everything except the visible openings.
    profs = adsk.core.ObjectCollection.create()
    for p in sk.profiles:
        if p.profileLoops.count >= 2:
            profs.add(p)
    ext_in = comp.features.extrudeFeatures.createInput(profs, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ext_in.setSymmetricExtent(val(P["front_t"]), True)
    ext = comp.features.extrudeFeatures.add(ext_in)
    front = ext.bodies.item(0)
    front.name = "Front (print)"
    fillet_all(comp, front, P["front_fillet"])
    front = comp.bRepBodies.itemByName("Front (print)")

    seat_depth = P["front_t"] - P["lip"]
    seat = comp.sketches.add(plane_at_y(comp, fb))
    seat.name = "Lens seats"
    for s in (-1, 1):
        lens = closed_spline(seat, [(s * x, fb, z) for x, z in LENS_RIGHT])
        offset_curve(seat, lens, (s * lcx, fb, 60.0), P["lens_clear"])
    si = comp.features.extrudeFeatures.createInput(all_profiles(seat), adsk.fusion.FeatureOperations.CutFeatureOperation)
    si.setSymmetricExtent(val(2 * seat_depth), True)
    si.participantBodies = [front]
    comp.features.extrudeFeatures.add(si)
    style(comp.bRepBodies.itemByName("Front (print)"), looks["frame"])

    # Curved lens cut from a base-curve shell. It touches the lip where the lip's inner edge is closest to the
    # lens centre (top/bottom); measuring sag there, not at the lens edge, keeps the bulge from biting into the lip.
    r, ct = P["lens_base_r"], P["lens_ct"]
    half_h = min_radius(LENS_RIGHT, lcx, lcz) - P["lip_inset"]
    sag = r - (r * r - half_h * half_h) ** 0.5
    y_centre = (ff - P["lip"]) + sag
    for s, label in ((-1, "Left lens (ThunderClap)"), (1, "Right lens (ThunderClap)")):
        lsk = comp.sketches.add(comp.xZConstructionPlane)
        lsk.name = label
        closed_spline(lsk, [(s * x, 0, z) for x, z in LENS_RIGHT])
        li = comp.features.extrudeFeatures.createInput(lsk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        li.setSymmetricExtent(val(40.0), True)
        prism = comp.features.extrudeFeatures.add(li).bodies.item(0)
        prism.name = label
        centre = (s * lcx, y_centre - r, lcz)
        shell = tbm.createSphere(pt(*centre), cm(r))
        tbm.booleanOperation(shell, tbm.createSphere(pt(*centre), cm(r - ct)), adsk.fusion.BooleanTypes.DifferenceBooleanType)
        bf = comp.features.baseFeatures.add()
        bf.startEdit()
        comp.bRepBodies.add(shell, bf)
        bf.finishEdit()
        tools = adsk.core.ObjectCollection.create()
        tools.add(bf.bodies.item(0))
        ci = comp.features.combineFeatures.createInput(comp.bRepBodies.itemByName(label), tools)
        ci.operation = adsk.fusion.FeatureOperations.IntersectFeatureOperation
        ci.isKeepToolBodies = False
        comp.features.combineFeatures.add(ci)
        style(comp.bRepBodies.itemByName(label), looks["glass"], 0.25)

    add_lens_retainers(comp, looks, lcx, lcz, y_centre - r)


def outline_hits(loop, axis, value):
    """Coordinates where a closed polyline crosses x=value (axis 0, returns z's) or z=value (axis 1, returns x's)."""
    hits = []
    for a, b in zip(loop, loop[1:] + loop[:1]):
        if (a[axis] - value) * (b[axis] - value) <= 0 and a[axis] != b[axis]:
            t = (value - a[axis]) / (b[axis] - a[axis])
            hits.append(a[1 - axis] + (b[1 - axis] - a[1 - axis]) * t)
    return hits


def add_lens_retainers(comp, looks, lcx, lcz, sphere_y):
    """Three press-fit tabs per lens (bottom, outer side, inner side) instead of glue.

    A flat ring can't work: the curved lens's corners already reach the frame's back face. Each tab instead
    sits 0.2mm behind the lens's own back surface at that spot, and pushes into a notch open to the back face.
    """
    clear, depth, width, over, t = P["lens_clear"], 1.0, 6.0, 1.5, 1.0
    fb = -P["front_t"] / 2
    back_r = P["lens_base_r"] - P["lens_ct"]
    bottom_z = min(outline_hits(LENS_RIGHT, 0, lcx)) - clear
    outer_x = max(outline_hits(LENS_RIGHT, 1, -6.0)) + clear
    inner_x = min(outline_hits(LENS_RIGHT, 1, 2.0)) - clear
    # (x0, x1, z0, z1) of each tab in the right lens, and the radius of its rearmost point over the lens
    tabs = [
        (lcx - width / 2, lcx + width / 2, bottom_z - depth, bottom_z + over),
        (outer_x - over, outer_x + depth, -6.0 - width / 2, -6.0 + width / 2),
        (inner_x - depth, inner_x + over, 2.0 - width / 2, 2.0 + width / 2),
    ]
    k = 0.05
    for s, side in ((1, "R"), (-1, "L")):
        notches = []
        for i, (x0, x1, z0, z1) in enumerate(tabs):
            far = max(((x - lcx) ** 2 + (z - lcz) ** 2) ** 0.5 for x in (x0, x1) for z in (z0, z1))
            y_back = sphere_y + (back_r * back_r - far * far) ** 0.5
            ty1 = min(y_back - 0.2, -1.0)
            ty0 = ty1 - t
            xa, xb = sorted((s * x0, s * x1))
            notches.append(box_span(xa - k, xb + k, fb - 0.1, ty1 + k, z0 - k, z1 + k))
            solid(comp, "Lens retainer tab {}{} (print)".format(side, i + 1), box_span(xa, xb, ty0, ty1, z0, z1), looks["frame"])
        cut_from(comp, "Front (print)", notches)


def build_temple(comp, looks, side, y_start, label, battery_pocket=False):
    pi = comp.constructionPlanes.createInput()
    x_plane = P["temple_x"] if side > 0 else -(P["temple_x"] + P["temple_t"])
    pi.setByOffset(comp.yZConstructionPlane, val(x_plane))
    plane = comp.constructionPlanes.add(pi)
    plane.isLightBulbOn = False
    sk = comp.sketches.add(plane)
    sk.name = label
    closed_spline(sk, [(x_plane, y, z) for y, z in temple_points(y_start)])
    ei = comp.features.extrudeFeatures.createInput(
        sk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation
    )
    ei.setDistanceExtent(False, val(P["temple_t"]))
    temple = comp.features.extrudeFeatures.add(ei).bodies.item(0)
    temple.name = label
    fillet_all(comp, temple, P["temple_fillet"])
    # The fitted spline overshoots past y_start at its front corners; trim to a flat face.
    cut_from(comp, label, [box_span(x_plane - 1, x_plane + P["temple_t"] + 1, y_start, y_start + 15, -40, 60)])
    rail_x, rail_z0, rail_z1 = P["rail_right"] if side > 0 else P["rail_left"]
    join_to(comp, label, [t_rail(rail_x, y_start, rail_z0, rail_z1)])
    temple = comp.bRepBodies.itemByName(label)
    style(temple, looks["frame"], 0.55 if battery_pocket else None)
    if battery_pocket:
        cut_battery_pocket(comp, temple, looks, y_start)


def t_rail(x, y_face, z0, z1):
    """Male T-rail standing forward (+Y) off a face at y_face; slides down into a matching t_slot."""
    (sw, sd), (fw, fd) = P["rail_stem"], P["rail_flange"]
    rail = box_span(x - sw / 2, x + sw / 2, y_face - 0.01, y_face + sd, z0, z1)
    tbm.booleanOperation(rail, box_span(x - fw / 2, x + fw / 2, y_face + sd, y_face + sd + fd, z0, z1),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    return rail


def t_slot(x, y_face, z_stop, sc):
    """Female T-slot cut forward (+Y) from y_face, open to the top, with a floor at z_stop so the rail can't drop through."""
    (sw, sd), (fw, fd) = P["rail_stem"], P["rail_flange"]
    return [
        box_span(x - sw / 2 - sc, x + sw / 2 + sc, y_face - 0.1, y_face + sd + sc, z_stop - sc, 60),
        box_span(x - fw / 2 - sc, x + fw / 2 + sc, y_face + sd - sc, y_face + sd + fd + sc, z_stop - sc, 60),
    ]


def join_to(comp, target_name, tbodies):
    bf = comp.features.baseFeatures.add()
    bf.startEdit()
    for t in tbodies:
        comp.bRepBodies.add(t, bf)
    bf.finishEdit()
    tools = adsk.core.ObjectCollection.create()
    for i in range(bf.bodies.count):
        tools.add(bf.bodies.item(i))
    ci = comp.features.combineFeatures.createInput(comp.bRepBodies.itemByName(target_name), tools)
    ci.operation = adsk.fusion.FeatureOperations.JoinFeatureOperation
    ci.isKeepToolBodies = False
    comp.features.combineFeatures.add(ci)


def cut_battery_pocket(comp, temple, looks, y_start):
    """Cell and switch sit behind one pencil-box lid that slides out through the arm's front face.

    The lid's tongues ride in grooves behind a 0.8mm lip, so it can't come out sideways; once the arm is slid
    onto the frame, the frame's back face blocks the lid. Slide the arm up and off to reach the battery.
    """
    c, lid, sc = P["clear"], P["lid_t"], P["slide_clear"]
    tongue, lip = P["tongue"], P["lip_x"]
    x_inner = P["temple_x"]
    x_cell0 = x_inner + lid + sc
    cell_l, cell_h = P["bat_l"] + 2 * c, P["bat_h"] + 2 * c
    y1 = y_start - P["pocket_y0"]
    y0 = y1 - cell_l
    cz = P["bat_cz"]
    z_open0, z_open1 = cz - cell_h / 2, cz + cell_h / 2

    sw_y1 = y0 - 1.0
    sw_y0 = sw_y1 - P["sw_l"]
    sw_z0 = 8.6
    sw_z1 = sw_z0 + P["sw_h"]
    sw_yc = (sw_y0 + sw_y1) / 2
    lid_end = sw_y0 - 0.5

    cut_from(comp, temple.name, [
        box_span(x_cell0, x_cell0 + P["bat_t"] + 2 * c, y0, y1, z_open0, z_open1),
        box_span(x_cell0, x_cell0 + P["sw_w"], sw_y0, sw_y1, sw_z0, sw_z1),
        box_span(x_cell0 + 0.8, x_cell0 + 3.2, sw_yc - 2.6, sw_yc + 2.6, sw_z0 - 4.4, sw_z0 + 0.1),
        box_span(x_cell0, x_cell0 + 3.0, sw_y1 - 0.2, y0 + 0.2, sw_z0 + 0.5, sw_z1 - 0.5),
        # lid opening + tongue grooves, running out through the front face
        box_span(x_inner - 1, x_inner + lid + sc, lid_end - sc, y_start + 1, z_open0 - sc, z_open1 + sc),
        box_span(x_inner + lip - sc, x_inner + lid + sc, lid_end - sc, y_start + 1,
                 z_open0 - tongue - sc, z_open1 + tongue + sc),
    ])

    bx = x_cell0 + c + P["bat_t"] / 2
    solid(comp, "LiPo 601452 500mAh", box(bx, (y0 + y1) / 2, cz, P["bat_t"], P["bat_l"], P["bat_h"]), looks["battery"])
    solid(comp, "Slide switch SS12D00 (body)", box(x_cell0 + P["sw_w"] / 2, sw_yc, sw_z0 + 1.75, 3.5, 8.6, 3.5), looks["pcb"])
    solid(comp, "Slide switch SS12D00 (knob)", box(x_cell0 + P["sw_w"] / 2, sw_yc, sw_z0 - 1.5, 1.5, 1.5, 3.0), looks["pcb"])

    lid_body = box_span(x_inner, x_inner + lid, lid_end, y_start, z_open0, z_open1)
    tbm.booleanOperation(lid_body, box_span(x_inner + lip, x_inner + lid, lid_end, y_start,
                                            z_open0 - tongue, z_open1 + tongue),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    solid(comp, "Battery sliding lid (print)", lid_body, looks["frame"], 0.8)


def build_camera_block(root, looks):
    """Left hinge block behind the front, XIAO face-forward; the camera looks past the end piece.

    The board drops in from the top (the cavity walls line the camera up with its window) and a pencil-box cap
    slides in from the outer side. The back wall carries the T-slot the left arm hangs on.
    """
    comp = new_component(root, "Left hinge block: camera + XIAO")
    w, c, sc = P["wall"], P["clear"], P["slide_clear"]
    cap_t, cap_tongue = 2.0, 0.6
    xl, xw, xs = P["xiao_len"], P["xiao_w"], P["xiao_stack"]
    x_inner = -(P["temple_x"] + 2.0)
    x_outer = x_inner - (xw + 2 * c + 2 * w + P["ant_gap"])
    y_front = -P["front_t"] / 2
    y_back = y_front - (w + xs + 2 * c + P["block_back_wall"])
    cz = P["xiao_cz"]
    z0, z1 = cz - xl / 2 - c - w, cz + xl / 2 + c + cap_t
    bx = x_inner - w - c - xw / 2
    stack_front = y_front - w - c
    stack_back = stack_front - xs
    by = (stack_front + stack_back) / 2
    # The USB/SD end points down; the camera sits toward it and slightly outboard.
    cam_x, cam_z = bx - P["cam_across"], cz - P["cam_along"]
    bottom = cz - xl / 2

    rounded_shell(
        comp, "Camera block (print)",
        box_span(x_outer, x_inner, y_back, y_front, z0, z1),
        [
            box_span(x_outer + w, x_inner - w, stack_back - c, stack_front + c, bottom - c, cz + xl / 2 + c),
            box(cam_x, y_front - w / 2, cam_z, P["cam_win"], w + 1.0, P["cam_win"]),
            box_span(bx + P["port_w0"], bx + P["port_w1"], stack_back - c, stack_back + P["port_depth"],
                     z0 - 0.5, bottom + 0.1),
            # a USB-C cable's moulded head (~12.4 x 6.5) has to reach the port 3.4mm inside the block
            box(bx, by + P["usb_dy"], (z0 + bottom) / 2, P["usb_plug"][0], P["usb_plug"][1], bottom - z0 + 1.2),
        ],
        P["pod_fillet"], looks["frame"], 0.55,
    )
    cav0, cav1 = stack_back - c, stack_front + c
    cut_from(comp, "Camera block (print)", [
        # pencil-box cap: open top, tongue grooves in the front/back walls, entry through the outer wall
        box_span(x_outer - 1, x_inner - w, cav0, cav1, z1 - cap_t - sc, z1 + 1),
        box_span(x_outer - 1, x_inner - w, cav0 - cap_tongue - sc, cav1 + cap_tongue + sc,
                 z1 - cap_t - sc, z1 - cap_t / 2 + sc),
    ] + t_slot(P["rail_left"][0], y_back, P["rail_left"][1], sc))
    cap = box_span(x_outer, x_inner - w - sc, cav0 + sc, cav1 - sc, z1 - cap_t, z1)
    tbm.booleanOperation(cap, box_span(x_outer, x_inner - w - sc, cav0 - cap_tongue, cav1 + cap_tongue,
                                       z1 - cap_t, z1 - cap_t / 2),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    solid(comp, "Camera block sliding cap (print)", cap, looks["frame"], 0.55)
    place_step(comp, P["xiao_step"], P["xiao_step_centre"], (bx, by, cz))
    solid(comp, "U.FL antenna (size unverified)", box_span(x_outer + w, x_outer + w + 0.8, by - 6, by + 6, cz - 8, cz + 8), looks["lensblack"])
    return y_back


def place_step(comp, path, step_centre, target):
    """Imports a vendor STEP into comp, rotated so its x (long axis) runs down -Z, its y (camera up) faces +Y
    forward, and its z (width) runs along +X, with step_centre landing on target."""
    # Imported at the root: nested under comp, Fusion moved the sub-assemblies but left the top PCB body behind.
    root = comp.parentDesign.rootComponent
    before = root.occurrences.count
    opts = app.importManager.createSTEPImportOptions(os.path.expanduser(path))
    app.importManager.importToTarget(opts, root)
    if root.occurrences.count == before:
        return
    occ = root.occurrences.item(root.occurrences.count - 1)
    sx, sy, sz = step_centre
    tx, ty, tz = target
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(
        pt(tx - sz, ty - sy, tz + sx),
        adsk.core.Vector3D.create(0, 0, -1),
        adsk.core.Vector3D.create(0, 1, 0),
        adsk.core.Vector3D.create(1, 0, 0),
    )
    try:
        occ.transform2 = m
    except AttributeError:
        occ.transform = m
    # In a parametric design a moved occurrence snaps back on the next recompute unless its position is captured.
    comp.parentDesign.snapshots.add()


def build_optics_pod(root, looks):
    comp = new_component(root, "Optics pod (HUD)")
    w, s = P["wall"], P["cube"]
    ex, ey, ez = P["eye"]
    inner = P["oled_pcb"] + 2 * P["oled_shim"]
    ccx, ccz = ex + P["cube_dx"], ez + P["cube_dz"]
    ccy = P["front_t"] / 2 + w + inner / 2

    lens_x0 = ccx + s / 2 + 0.5
    lens_x1 = lens_x0 + P["lens_t"]
    lens_mid = (lens_x0 + lens_x1) / 2
    oled_x0 = lens_mid + P["lens_f"] * P["oled_gap_ratio"]
    oled_x1 = oled_x0 + P["oled_t"]
    slide_end = lens_mid + P["lens_f"] * P["focus_max"] + P["oled_t"]
    half = inner / 2

    # Slim cage around the cube (so it blocks little of the lens); a solid block with a round seat holds the
    # 22mm lens; the OLED rides in a sled in the square tunnel beyond it, held at focus by spacer rods.
    window = s - 3.0
    cage = box(ccx, ccy, ccz, s + 2 * w, s + 2 * w, s + 2 * w)
    tunnel = box_span(lens_x0 - 1, slide_end + w, ccy - half - w, ccy + half + w, ccz - half - w, ccz + half + w)
    tbm.booleanOperation(cage, tunnel, adsk.fusion.BooleanTypes.UnionBooleanType)
    rounded_shell(
        comp, "Optics housing (print)",
        cage,
        [
            box(ccx, ccy, ccz, s + 0.3, s + 0.3, s + 0.3),
            cyl((lens_x0 - 0.2, ccy, ccz), (lens_x1 + 0.1, ccy, ccz), P["lens_d"] / 2 + 0.15),
            box_span(lens_x1, slide_end, ccy - half, ccy + half, ccz - half, ccz + half),
            box(ccx, ccy, ccz, window, s + 4 * w, window),
            box_span(ccx, lens_x0 + 0.5, ccy - s / 2 + 1, ccy + s / 2 - 1, ccz - s / 2 + 1, ccz + s / 2 - 1),
        ],
        1.0, looks["frame"], 0.35,
    )

    # Tunnel lid: pencil-box plate that slides in through the far end wall and stops against the lens block.
    sc, lid_t, lid_tongue = P["slide_clear"], 2.0, 0.6
    top = ccz + half + w
    end_x = slide_end + w
    cut_from(comp, "Optics housing (print)", [
        box_span(lens_x1, end_x + 1, ccy - half, ccy + half, top - lid_t - sc, top + 1),
        box_span(lens_x1, end_x + 1, ccy - half - lid_tongue - sc, ccy + half + lid_tongue + sc,
                 top - lid_t - sc, top - lid_t / 2 + sc),
    ])
    lid = box_span(lens_x1 + sc, end_x, ccy - half + sc, ccy + half - sc, top - lid_t, top)
    tbm.booleanOperation(lid, box_span(lens_x1 + sc, end_x, ccy - half - lid_tongue, ccy + half + lid_tongue,
                                       top - lid_t, top - lid_t / 2),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    solid(comp, "Optics tunnel sliding lid (print)", lid, looks["frame"], 0.35)

    # Cube + lens section: its top half lifts off on two friction dowels (no glue), so the cube and lens can come out.
    housing = comp.bRepBodies.itemByName("Optics housing (print)")
    cap_region = box_span(ccx - s, lens_x1, ccy - half - 2 * w, ccy + half + 2 * w, ccz, top + 1)
    cap = tbm.copy(housing)
    tbm.booleanOperation(cap, cap_region, adsk.fusion.BooleanTypes.IntersectionBooleanType)
    cut_from(comp, "Optics housing (print)", [tbm.copy(cap_region)])
    pin_x = (lens_x0 + lens_x1) / 2
    cap_pins = [(pin_x, ccy - 14.0), (pin_x, ccy + 14.0)]
    for x, y in cap_pins:
        tbm.booleanOperation(cap, cyl((x, y, ccz - 0.1), (x, y, ccz + 3.2), P["hole_r"]),
                             adsk.fusion.BooleanTypes.DifferenceBooleanType)
    cut_from(comp, "Optics housing (print)", [cyl((x, y, ccz + 0.1), (x, y, ccz - 3.2), P["hole_r"]) for x, y in cap_pins])
    solid(comp, "Optics cube + lens cap (print)", cap, looks["frame"], 0.35)
    for x, y in cap_pins:
        solid(comp, "Cap friction dowel", cyl((x, y, ccz - 3.0), (x, y, ccz + 3.0), P["pin_r"]), looks["pin"])

    # Display sled: a picture frame around the OLED that slides in the tunnel; a front lip with a window over
    # the active area, open at the back for the wires.
    sled_wall, lip = 1.2, P["sled_lip"]
    so = P["oled_pcb"] + 2 * sled_wall
    sled = box_span(oled_x0 - lip, oled_x1, ccy - so / 2, ccy + so / 2, ccz - so / 2, ccz + so / 2)
    tbm.booleanOperation(sled, box_span(oled_x0 - 0.01, oled_x1 + 1, ccy - P["oled_pcb"] / 2 - P["clear"],
                                        ccy + P["oled_pcb"] / 2 + P["clear"], ccz - P["oled_pcb"] / 2 - P["clear"],
                                        ccz + P["oled_pcb"] / 2 + P["clear"]),
                         adsk.fusion.BooleanTypes.DifferenceBooleanType)
    tbm.booleanOperation(sled, box(oled_x0 - lip / 2, ccy, ccz, lip + 0.2, P["oled_window"][0], P["oled_window"][1]),
                         adsk.fusion.BooleanTypes.DifferenceBooleanType)
    solid(comp, "Display sled (print)", sled, looks["frame"], 0.35)

    # Focus spacers: two rods in the tunnel's bottom corners in front of the sled and two behind it; swapping
    # rod lengths moves the display along the axis. The nominal pair is shown; the full set prints with the fit tests.
    front_len, rear_len = focus_spacer_lengths(P["oled_gap_ratio"], lens_mid, lens_x1, slide_end)
    rod = P["spacer_rod"]
    for sy in (-1, 1):
        ry = ccy + sy * (half - rod / 2 - 0.3)
        rz = ccz - half + rod / 2 + 0.3
        solid(comp, "Focus spacer (front, print)", box_span(lens_x1 + 0.05, lens_x1 + front_len, ry - rod / 2, ry + rod / 2,
                                                            rz - rod / 2, rz + rod / 2), looks["pin"])
        solid(comp, "Focus spacer (rear, print)", box_span(oled_x1 + 0.05, oled_x1 + rear_len, ry - rod / 2, ry + rod / 2,
                                                           rz - rod / 2, rz + rod / 2), looks["pin"])

    solid(comp, "Beam splitter cube 15mm", box(ccx, ccy, ccz, s, s, s), looks["glass"], 0.35)

    along = adsk.core.Vector3D.create(1, 1, 0)
    across = adsk.core.Vector3D.create(-1, 1, 0)
    along.normalize()
    across.normalize()
    diag = adsk.core.OrientedBoundingBox3D.create(pt(ccx, ccy, ccz), along, across, cm(s * 1.38), cm(0.3), cm(s * 0.98))
    solid(comp, "50/50 coating (diagonal)", tbm.createBox(diag), looks["mirror"])
    solid(comp, "Lens 22mm f27", cyl((lens_x0, ccy, ccz), (lens_x1, ccy, ccz), P["lens_d"] / 2), looks["glass"], 0.4)
    solid(comp, "SSD1306 OLED module",
          box_span(oled_x0, oled_x1, ccy - P["oled_pcb"] / 2, ccy + P["oled_pcb"] / 2,
                   ccz - P["oled_pcb"] / 2, ccz + P["oled_pcb"] / 2), looks["pcb"])
    solid(comp, "Light path: OLED to cube", cyl((oled_x0, ccy, ccz), (ccx, ccy, ccz), 0.4), looks["ray"])
    solid(comp, "Light path: cube to eye", cyl((ccx, ccy, ccz), (ex, ey, ez), 0.4), looks["ray"])


def focus_spacer_lengths(ratio, lens_mid, lens_x1, slide_end):
    """Front and rear rod lengths that hold the sled with the OLED face at lens_mid + ratio * f."""
    oled_face = lens_mid + P["lens_f"] * ratio
    front = oled_face - P["sled_lip"] - lens_x1 - 0.1
    rear = slide_end - (oled_face + P["oled_t"]) - 0.1
    return front, rear


def coupon_body():
    """Push a filament dowel into each hole; the smallest snug one becomes hole_r * 2.

    Holes run smallest at -X to largest at +X; a notch marks the -X end.
    """
    holes = P["coupon_holes"]
    pitch, depth = 8.0, P["hole_depth"]
    length = pitch * (len(holes) + 1)
    block = box_span(-length / 2, length / 2, -5, 5, 0, 6)
    for i, dia in enumerate(holes):
        x = -length / 2 + pitch * (i + 1)
        tbm.booleanOperation(block, cyl((x, 0, 6.1), (x, 0, 6 - depth), dia / 2),
                             adsk.fusion.BooleanTypes.DifferenceBooleanType)
    tbm.booleanOperation(block, box_span(-length / 2 - 0.1, -length / 2 + 1.5, -5.1, 5.1, 4.5, 6.1),
                         adsk.fusion.BooleanTypes.DifferenceBooleanType)
    return block


def rotation(axis, degrees):
    m = adsk.core.Matrix3D.create()
    m.setToRotation(degrees * 3.141592653589793 / 180, adsk.core.Vector3D.create(*axis), pt(0, 0, 0))
    return m


# Each fit-test piece: (label, component, body, section box (x0, x1, y0, y1, z0, z1), print rotation).
# Rotations put a flat face on the bed: lens corner front-face down, temple slices outer-face down.
FIT_TESTS = [
    ("lens seat corner", "Frame (print)", "Front (print)", (42, 70, -4, 4, -30, -6), ((1, 0, 0), -90)),
    ("battery pocket slice", "Frame (print)", "Right temple (battery inside)", (64, 77, -32, -20, 3, 26), ((0, 1, 0), 90)),
    ("switch pocket", "Frame (print)", "Right temple (battery inside)", (64, 77, -73, -60.5, 3, 26), ((0, 1, 0), 90)),
    ("USB-C + SD opening", "Left hinge block: camera + XIAO", "Camera block (print)", (-93, -67, -23, -2, -4, 3), None),
]


def pod_stations():
    """(lens_x0, lens_x1, lens_mid, slide_end) along the optics pod axis, as build_optics_pod lays them out."""
    lens_x0 = P["eye"][0] + P["cube_dx"] + P["cube"] / 2 + 0.5
    lens_x1 = lens_x0 + P["lens_t"]
    lens_mid = (lens_x0 + lens_x1) / 2
    return lens_x0, lens_x1, lens_mid, lens_mid + P["lens_f"] * P["focus_max"] + P["oled_t"]


def slide_fit_bodies():
    """A block with three T-slots at the slide_test clearances (smallest at -X, notch marks that end) and one rail.

    Use the clearance whose slot the rail slides through smoothly without wobble as slide_clear.
    """
    (sw, sd), (fw, fd) = P["rail_stem"], P["rail_flange"]
    gaps, pitch, length, height = P["slide_test"], 10.0, 20.0, 6.0
    width = pitch * (len(gaps) + 1)
    block = box_span(-width / 2, width / 2, 0, length, 0, height)
    for i, g in enumerate(gaps):
        x = -width / 2 + pitch * (i + 1)
        tbm.booleanOperation(block, box_span(x - sw / 2 - g, x + sw / 2 + g, -0.1, length + 0.1, height - sd - g, height + 0.1),
                             adsk.fusion.BooleanTypes.DifferenceBooleanType)
        tbm.booleanOperation(block, box_span(x - fw / 2 - g, x + fw / 2 + g, -0.1, length + 0.1,
                                             height - sd - fd - g, height - sd + g),
                             adsk.fusion.BooleanTypes.DifferenceBooleanType)
    tbm.booleanOperation(block, box_span(-width / 2 - 0.1, -width / 2 + 1.5, -0.1, 2.0, height - 1.5, height + 0.1),
                         adsk.fusion.BooleanTypes.DifferenceBooleanType)
    rail = box_span(-sw / 2, sw / 2, 0, length, height - sd, height + 2.0)
    tbm.booleanOperation(rail, box_span(-fw / 2, fw / 2, 0, length, height - sd - fd, height - sd),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    tbm.booleanOperation(rail, box_span(-4.0, 4.0, 0, length, height + 0.5, height + 2.5),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    return block, rail


def spacer_comb(lengths):
    """Spacer rods (one pair per focus step, shortest first) on a snap-off spine so they don't get lost."""
    rod, gap, spine = P["spacer_rod"], 2.0, 2.0
    comb = None
    y = 0.0
    for length in lengths:
        for _ in range(2):
            r = box_span(spine, spine + length, y, y + rod, 0, rod)
            if comb is None:
                comb = r
            else:
                tbm.booleanOperation(comb, r, adsk.fusion.BooleanTypes.UnionBooleanType)
            y += rod + 0.6
        y += gap
    tbm.booleanOperation(comb, box_span(0, spine + 0.3, 0, y - gap - 0.6, 0, 0.8),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    return comb


def build_fit_test(design, looks):
    """One small print that checks every tight fit before any full part is printed; also exports each piece as STL."""
    comp = new_component(design.rootComponent, "Fit-test plate (print first)")
    slide_block, slide_rail = slide_fit_bodies()
    tbm.transform(slide_rail, rotation((1, 0, 0), 180))
    _, lens_x1, lens_mid, slide_end = pod_stations()
    lengths = [focus_spacer_lengths(k, lens_mid, lens_x1, slide_end) for k in P["focus_steps"]]
    pieces = [
        ("dowel hole sizes", coupon_body()),
        ("slide fit T-slots 0.15-0.25-0.35", slide_block),
        ("slide fit T-rail", slide_rail),
        ("focus spacers front", spacer_comb([f for f, _ in lengths])),
        ("focus spacers rear", spacer_comb([r for _, r in lengths])),
    ]
    for label, comp_name, body_name, (x0, x1, y0, y1, z0, z1), rot in FIT_TESTS:
        src = design.allComponents.itemByName(comp_name).bRepBodies.itemByName(body_name)
        piece = tbm.copy(src)
        tbm.booleanOperation(piece, box_span(x0, x1, y0, y1, z0, z1), adsk.fusion.BooleanTypes.IntersectionBooleanType)
        if rot:
            tbm.transform(piece, rotation(*rot))
        pieces.append((label, piece))

    out_dir = os.path.expanduser(P["fit_test_dir"])
    os.makedirs(out_dir, exist_ok=True)
    x_cursor, bed_y, bed_z = -60.0, -120.0, -60.0
    for label, piece in pieces:
        b = piece.boundingBox
        move = adsk.core.Matrix3D.create()
        move.translation = adsk.core.Vector3D.create(
            cm(x_cursor) - b.minPoint.x, cm(bed_y) - (b.minPoint.y + b.maxPoint.y) / 2, cm(bed_z) - b.minPoint.z)
        tbm.transform(piece, move)
        x_cursor += (b.maxPoint.x - b.minPoint.x) * 10 + 6.0
        body = solid(comp, "Fit test: {} (print)".format(label), piece, looks["frame"])
        opts = design.exportManager.createSTLExportOptions(body, os.path.join(out_dir, "fit-test - {}.stl".format(label)))
        opts.meshRefinement = adsk.fusion.MeshRefinementSettings.MeshRefinementHigh
        design.exportManager.execute(opts)


def build_reference(root, looks):
    comp = new_component(root, "Reference (not printed)")
    solid(comp, "Right eye", tbm.createSphere(pt(*P["eye"]), cm(12)), looks["eye"], 0.5)


def cut_from(comp, target_name, tbodies):
    target = comp.bRepBodies.itemByName(target_name)
    bf = comp.features.baseFeatures.add()
    bf.startEdit()
    for t in tbodies:
        comp.bRepBodies.add(t, bf)
    bf.finishEdit()
    tools = adsk.core.ObjectCollection.create()
    for i in range(bf.bodies.count):
        tools.add(bf.bodies.item(i))
    ci = comp.features.combineFeatures.createInput(target, tools)
    ci.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
    ci.isKeepToolBodies = False
    comp.features.combineFeatures.add(ci)


def groove_along(right_path, y0, y1, h):
    """Chain of overlapping boxes following a mirrored (x, z) path, cut y0..y1 deep."""
    path = [(-x, z) for x, z in reversed(right_path) if x != 0] + list(right_path)
    ydir = adsk.core.Vector3D.create(0, 1, 0)
    boxes = []
    for (xa, za), (xb, zb) in zip(path, path[1:]):
        length = ((xb - xa) ** 2 + (zb - za) ** 2) ** 0.5
        along = adsk.core.Vector3D.create(xb - xa, 0, zb - za)
        along.normalize()
        obb = adsk.core.OrientedBoundingBox3D.create(
            pt((xa + xb) / 2, (y0 + y1) / 2, (za + zb) / 2), along, ydir, cm(length + h), cm(y1 - y0), cm(h)
        )
        boxes.append(tbm.createBox(obb))
    return boxes


def y_holes(points, y_a, y_b):
    return [cyl((x, y_a, z), (x, y_b, z), P["hole_r"]) for x, z in points]


def add_joinery(design, looks, block_back):
    """Joints, wire routing and the wire cover.

    Glued (permanent): camera block and optics pod to the front, on 1.75mm filament dowels that are blind in the
    frame and end flush inside the housing wall. Removable: both arms hang on T-rails; the wire cover press-fits.
    """
    frame = design.allComponents.itemByName("Frame (print)")
    cam = design.allComponents.itemByName("Left hinge block: camera + XIAO")
    pod = design.allComponents.itemByName("Optics pod (HUD)")
    pins = new_component(design.rootComponent, "Dowels (1.75mm filament)")
    w, d = P["wall"], P["hole_depth"]
    fb, ff = -P["front_t"] / 2, P["front_t"] / 2
    end_x, end_z = WIRE_PATH[-1]
    wz1 = end_z + P["wire_h"] / 2
    dowels = []

    # camera block -> back of front
    a = [(-71.0, 12.0), (-71.5, 16.5)]
    cut_from(frame, "Front (print)", y_holes(a, fb - 0.1, fb + d))
    cut_from(cam, "Camera block (print)", y_holes(a, fb + 0.1, fb - w - 0.1))
    dowels += [((x, fb - w, z), (x, fb + d - 0.2, z)) for x, z in a]

    # right arm hangs on a T-rail in a slot in the back of the front's end piece (the left one's slot is in the
    # camera block's back wall)
    cut_from(frame, "Front (print)", t_slot(P["rail_right"][0], fb, P["rail_right"][1], P["slide_clear"]))

    # optics pod base -> face of front: below the lift-off cap's split line, inboard of the right arm's T-slot
    e = [(65.0, 1.0), (65.0, 8.0)]
    cut_from(frame, "Front (print)", y_holes(e, ff + 0.1, ff - d))
    cut_from(pod, "Optics housing (print)", y_holes(e, ff - 0.1, ff + w + 0.1))
    dowels += [((x, ff - d + 0.2, z), (x, ff + w, z)) for x, z in e]

    # wires: groove behind the brow, into the battery temple, through the front into the pod, into the camera
    # block. A shallow rebate over the groove takes a press-fit cover strip; it stops short of the hinge areas.
    cover_path = [(x, z) for x, z in WIRE_PATH if x <= P["cover_end_x"]]
    rebate_h = P["wire_h"] + 2 * P["cover_overlap"]
    cut_from(frame, "Front (print)", groove_along(WIRE_PATH, fb - 0.1, fb + 2.0, P["wire_h"]) +
             groove_along(cover_path, fb - 0.1, fb + P["cover_t"], rebate_h) + [
        cyl((end_x, fb + 1.0, end_z), (end_x, ff + 0.1, end_z), P["wire_r"]),
    ])
    strip_parts = groove_along(cover_path, fb, fb + P["cover_t"], rebate_h - P["clear"])
    strip = strip_parts[0]
    for part in strip_parts[1:]:
        tbm.booleanOperation(strip, part, adsk.fusion.BooleanTypes.UnionBooleanType)
    solid(frame, "Wire cover strip (print)", strip, looks["frame"])
    cut_from(pod, "Optics housing (print)", [cyl((end_x, ff - 0.1, end_z), (end_x, ff + w + 0.1, end_z), P["wire_r"])])
    cut_from(frame, "Right temple (battery inside)", [
        box_span(end_x - 3.0, end_x + 1.0, fb - P["pocket_y0"] - 0.5, fb + 0.1, end_z - P["wire_h"] / 2, wz1),
    ])
    cut_from(cam, "Camera block (print)", [cyl((-end_x, fb + 0.1, end_z), (-end_x, fb - w - 0.1, end_z), P["wire_r"])])

    for comp in (cam, pod):
        for i in range(comp.bRepBodies.count):
            b = comp.bRepBodies.item(i)
            if "(print)" in b.name:
                style(b, looks["frame"], 0.55)

    for i, (p0, p1) in enumerate(dowels):
        solid(pins, "Dowel {}".format(i + 1), cyl(p0, p1, P["pin_r"]), looks["pin"])


def run(context):
    ui = app.userInterface
    try:
        # Each run builds a fresh document; close earlier never-saved builds so tabs don't pile up.
        for doc in [app.documents.item(i) for i in range(app.documents.count)]:
            if doc.dataFile is None and doc.name.startswith("Untitled"):
                doc.close(False)
        app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(app.activeProduct)
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        root = design.rootComponent

        looks = {
            "frame": find_appearance("plastic", "glossy", "black") or find_appearance("plastic", "black"),
            "tint": find_appearance("glass", "gray") or find_appearance("glass", "smoke") or find_appearance("glass"),
            "glass": find_appearance("glass"),
            "pcb": find_appearance("plastic", "green") or find_appearance("green"),
            "lensblack": find_appearance("rubber", "black") or find_appearance("black"),
            "battery": find_appearance("aluminum") or find_appearance("metal"),
            "mirror": find_appearance("mirror") or find_appearance("chrome"),
            "ray": find_appearance("red"),
            "eye": find_appearance("plastic", "white"),
            "pin": find_appearance("plastic", "white") or find_appearance("white"),
        }

        frame = new_component(root, "Frame (print)")
        build_front(frame, looks)
        left_back = build_camera_block(root, looks)
        build_temple(frame, looks, -1, left_back, "Left temple")
        build_temple(frame, looks, 1, -P["front_t"] / 2, "Right temple (battery inside)", battery_pocket=True)
        build_optics_pod(root, looks)
        build_reference(root, looks)
        add_joinery(design, looks, left_back)
        build_fit_test(design, looks)

        vp = app.activeViewport
        cam = vp.camera
        cam.eye = pt(0, 500, 0)
        cam.target = pt(0, 0, 0)
        cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
        vp.camera = cam
        vp.fit()
    except Exception:
        ui.messageBox("MemoryGlasses failed:\n{}".format(traceback.format_exc()))
