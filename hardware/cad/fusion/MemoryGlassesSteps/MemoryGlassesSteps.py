"""Builds "Memory Glasses v2 - Assembly steps": a new Fusion file that shows how to put v2 together, one step per
component, laid out in a grid.

Each step shows the parts already installed faded, the new parts in orange pulled back along the way they go in,
red arrows for that motion, and a label with the instruction. A PNG of every step goes to
~/projects/memory-glasses/hardware/cad/renders/assembly-steps/. The geometry comes from MemoryGlassesV2.build, so the steps
always match the current v2 design.
"""

import importlib
import math
import os
import sys
import traceback

import adsk.core
import adsk.fusion

FUSION_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
for sub in ("MemoryGlasses", "MemoryGlassesV2"):
    path = os.path.join(FUSION_DIR, sub)
    if path not in sys.path:
        sys.path.insert(0, path)
import MemoryGlassesV2 as v2  # noqa: E402

importlib.reload(v2)
v1 = v2.v1
P = v1.P
tbm, app = v1.tbm, v1.app

DOC_NAME = "Memory Glasses v2 - Assembly steps"
IMAGE_DIR = os.path.expanduser("~/projects/memory-glasses/hardware/cad/renders/assembly-steps")
# grid of steps in the XZ plane, so one front view shows them all
COLS, DX, DZ = 5, 290.0, 230.0

FRAME, CAM, POD = "Frame (print)", "Left hinge block: camera + XIAO", "Optics pod (HUD)"
# (component, body-name prefix) for each group of parts that goes in together
GROUPS = {
    "front": [(FRAME, v2.FRONT_UNIT)],
    "lenses": [(FRAME, "Left lens"), (FRAME, "Right lens")],
    "tabs": [(FRAME, "Lens retainer tab")],
    "xiao": [(CAM, "U.FL antenna")],
    "camcap": [(CAM, "Camera block sliding cap")],
    "strip": [(FRAME, "Wire cover strip")],
    "optics": [(POD, "Beam splitter cube"), (POD, "50/50 coating"), (POD, "Lens 22mm")],
    "podcap": [(POD, "Cap friction dowel"), (POD, "Optics cube + lens cap")],
    "display": [(POD, "SSD1306"), (POD, "Display sled"), (POD, "Focus spacer")],
    "tunnellid": [(POD, "Optics tunnel sliding lid")],
    "window": [(POD, "Tinted pod window")],
    "cell": [(FRAME, "LiPo"), (FRAME, "Slide switch")],
    "batlid": [(FRAME, "Battery sliding lid")],
    "rightarm": [(FRAME, "Right temple")],
    "leftarm": [(FRAME, "Left temple")],
}
# The XIAO board is an imported vendor model; its occurrences are found by this marker in their path.
VENDOR_MARKER = "ESP32"

RAISE = 45.0
# title, instruction, [(group, offset from installed position in mm, draw arrow)], groups installed after the step
# (None = the step's own groups)
STEPS = [
    ("Front unit", "Print the front unit standing up with a brim and tree supports (see hardware/cad/print/v2/README.md).\n"
     "Clean the slots and seats.",
     [("front", (0, 0, 0), False)], None),
    ("Frame lenses", "Drop each ThunderClap lens into its seat from the back (curved side forward).",
     [("lenses", (0, -35, 0), True)], None),
    ("Lens retainer tabs", "Press 3 tabs per lens into the notches behind it: bottom, outer side, inner side.",
     [("tabs", (0, -18, 0), True)], None),
    ("XIAO into the camera housing", "Solder the display (4 wires) and battery/switch (2 wires) leads to the XIAO first.\n"
     "Drop it in from the top, camera forward; the antenna lies flat on the outer wall.",
     [("xiao", (0, 0, RAISE), True)], None),
    ("Camera housing cap", "Slide the cap in from the outer side until it stops at the inner wall.",
     [("camcap", (-40, 0, 0), True)], None),
    ("Brow wires + cover strip", "Lay the wires in the groove behind the brow (camera to the right end piece),\n"
     "then press the cover strip over them.",
     [("wires", (0, -14, 0), True), ("strip", (0, -26, 0), True)], None),
    ("Cube + 22 mm lens", "Drop the beam-splitter cube (coating diagonal) and the 22 mm lens into the pod base.",
     [("optics", (0, 0, 38), True)], None),
    ("Lift-off cap", "Push the two filament friction dowels into the base, then lower the cube + lens cap onto them.",
     [("podcap", (0, 0, 32), True)], None),
    ("Display sled + focus spacers", "Seat the OLED in its sled (window over the screen), wires out the back.\n"
     "Slide spacers, sled, spacers into the tunnel from the far end. Swap spacer lengths to focus.",
     [("display", (55, 0, 0), True)], None),
    ("Tunnel lid", "Slide the tunnel lid in from the far end until it stops at the lens block.",
     [("tunnellid", (45, 0, 0), True)], None),
    ("Tinted window", "Press the tinted window into the recess on the pod's front.",
     [("window", (0, 18, 0), True)], None),
    ("Battery + switch into the right arm", "Solder the battery and switch leads, then set the cell and switch in the\n"
     "pocket from the inner side. Slide the battery lid in through the arm's front face.",
     [("rightarm", (0, 0, RAISE), False), ("cell", (-28, 0, RAISE), True), ("batlid", (0, 35, RAISE), True)], []),
    ("Right arm on", "Lower the right arm onto its rail in the frame's right end piece. The frame now holds\n"
     "the battery lid shut.",
     [("rightarm", (0, 0, RAISE), True), ("cell", (0, 0, RAISE), False), ("batlid", (0, 0, RAISE), False)],
     ["rightarm", "cell", "batlid"]),
    ("Left arm on", "Lower the left arm onto the rail in the camera housing's back wall.",
     [("leftarm", (0, 0, RAISE), True)], None),
    ("Done", "Charge over USB-C under the camera housing. Switch on with the slide switch on the right arm.",
     [], None),
]


def collect_parts(design):
    """Source bodies (or vendor-model proxies) for each group."""
    parts = {}
    for group, specs in GROUPS.items():
        found = []
        for comp_name, prefix in specs:
            comp = design.allComponents.itemByName(comp_name)
            if comp:
                found += [b for b in comp.bRepBodies if b.name.startswith(prefix)]
        parts[group] = found
    for occ in design.rootComponent.allOccurrences:
        if VENDOR_MARKER in occ.fullPathName:
            parts["xiao"] += [b for b in occ.bRepBodies]
    return parts


def wire_bodies():
    """The brow wires as one thin body: along the groove, into the pod, the battery arm and the camera housing."""
    fb, ff = -P["front_t"] / 2, P["front_t"] / 2
    y = fb + 1.0
    path = [(-x, z) for x, z in reversed(v1.WIRE_PATH) if x != 0] + list(v1.WIRE_PATH)
    pts = [(x, y, z) for x, z in path]
    end_x, end_z = v1.WIRE_PATH[-1]
    legs = [((end_x, y, end_z), (end_x, ff + 1.6, end_z)), ((end_x, y, end_z), (end_x, fb - 6.0, end_z)),
            ((-end_x, y, end_z), (-end_x, fb - 2.0, end_z))]
    body = None
    for a, b in list(zip(pts, pts[1:])) + legs:
        seg = v1.cyl(a, b, 0.5)
        if body is None:
            body = seg
        else:
            tbm.booleanOperation(body, seg, adsk.fusion.BooleanTypes.UnionBooleanType)
    return body


def bbox(bodies):
    lo, hi = [1e9] * 3, [-1e9] * 3
    for b in bodies:
        bb = b.boundingBox
        for i, (a, c) in enumerate(zip((bb.minPoint.x, bb.minPoint.y, bb.minPoint.z), (bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z))):
            lo[i], hi[i] = min(lo[i], a * 10), max(hi[i], c * 10)
    return lo, hi


def moved(tbody, d):
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(v1.cm(d[0]), v1.cm(d[1]), v1.cm(d[2]))
    tbm.transform(tbody, m)
    return tbody


def arrow(start, end):
    """Shaft + cone head from start to end (mm)."""
    v = [e - s for s, e in zip(start, end)]
    n = sum(c * c for c in v) ** 0.5
    if n < 8:
        return None
    u = [c / n for c in v]
    head = 6.0
    base = [e - c * head for e, c in zip(end, u)]
    shaft = v1.cyl(start, base, 0.8)
    tbm.booleanOperation(shaft, tbm.createCylinderOrCone(v1.pt(*base), v1.cm(2.4), v1.pt(*end), v1.cm(0.05)),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    return shaft


def add_label(comp, text, x0, z_top):
    """Instruction text in the step's own XZ sketch, so hiding the step hides its label too."""
    sk = comp.sketches.add(comp.xZConstructionPlane)
    sk.name = "Label"
    ti = sk.sketchTexts.createInput2(text, v1.cm(5.0))
    c1 = sk.modelToSketchSpace(v1.pt(x0, 0, z_top))
    c2 = sk.modelToSketchSpace(v1.pt(x0 + 270, 0, z_top - 45))
    ti.setAsMultiLine(c1, c2, adsk.core.HorizontalAlignments.LeftHorizontalAlignment,
                      adsk.core.VerticalAlignments.TopVerticalAlignment, 0)
    # the XZ plane's sketch axes run opposite to model X and Z, so unrotated text reads upside down from the front
    ti.angle = math.pi
    sk.sketchTexts.add(ti)


def build_steps(design, looks, parts):
    root = design.rootComponent
    ghost, new, red = looks["frame"], looks["new"], looks["arrow"]
    lo, hi = bbox(parts["xiao"])
    xiao_box = v1.box_span(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])
    installed, steps = [], []
    for i, (title, detail, items, installs) in enumerate(STEPS):
        col, row = i % COLS, i // COLS
        # seen from the front, model +X is on the viewer's left, so steps run toward -X to read left to right
        T = (-col * DX, 0.0, -row * DZ)
        name = "Step {:02d} - {}".format(i + 1, title)
        comp = v1.new_component(root, name)
        entries = []
        for g in installed:
            if g == "xiao":
                entries.append((moved(tbm.copy(xiao_box), T), "XIAO (shown as its outline)", ghost, 0.2))
            elif g == "wires":
                entries.append((moved(wire_bodies(), T), "Wires", ghost, 0.2))
            else:
                for b in parts[g]:
                    entries.append((moved(tbm.copy(b), T), b.name, ghost, 0.2))
        for g, off, with_arrow in items:
            srcs = parts.get(g, [])
            d = tuple(t + o for t, o in zip(T, off))
            if g == "wires":
                entries.append((moved(wire_bodies(), d), "Wires (30 AWG)", red, 1.0))
                c = (0.0, -P["front_t"] / 2 + 1.0, 17.0)
            else:
                for b in srcs:
                    entries.append((moved(tbm.copy(b), d), b.name, looks["pcb"] if g == "xiao" else new, 1.0))
                if not srcs:
                    continue
                blo, bhi = bbox(srcs)
                c = tuple((a + b) / 2 for a, b in zip(blo, bhi))
            if with_arrow:
                start = tuple(ci + t + o * 0.95 for ci, t, o in zip(c, T, off))
                end = tuple(ci + t + o * 0.2 for ci, t, o in zip(c, T, off))
                a = arrow(start, end)
                if a:
                    entries.append((a, "Arrow: {}".format(g), red, 1.0))
        bf = comp.features.baseFeatures.add()
        bf.startEdit()
        for t, _, _, _ in entries:
            comp.bRepBodies.add(t, bf)
        bf.finishEdit()
        for k, (_, label, look, opacity) in enumerate(entries):
            body = bf.bodies.item(k)
            body.name = label
            v1.style(body, look, opacity)
        text = "STEP {}  {}\n{}".format(i + 1, title.upper(), detail)
        try:
            add_label(comp, text, T[0] - 135, T[2] - 40)
        except Exception:
            pass
        installed += [g for g, _, _ in items] if installs is None else installs
        steps.append((name, T))
    return steps


def save_images(design, steps):
    os.makedirs(IMAGE_DIR, exist_ok=True)
    vp = app.activeViewport
    occs = {o.component.name: o for o in design.rootComponent.occurrences}
    for name, T in steps:
        for other, o in occs.items():
            if other.startswith("Step "):
                o.isLightBulbOn = other == name
        cam = vp.camera
        cam.isSmoothTransition = False
        # centred a little low so the label under the glasses and parts raised 45mm above both fit
        target = (T[0], -10, T[2] - 8)
        cam.target = v1.pt(*target)
        cam.eye = v1.pt(target[0] - 150, 360, target[2] + 140)
        cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
        cam.viewExtents = 20.0
        vp.camera = cam
        vp.refresh()
        vp.saveAsImageFile(os.path.join(IMAGE_DIR, "{}.png".format(name.split(" - ")[0].lower().replace(" ", "-"))), 1600, 1000)
    for other, o in occs.items():
        if other.startswith("Step "):
            o.isLightBulbOn = True
    cam = vp.camera
    cam.eye = v1.pt(-DX * (COLS - 1) / 2, 900, -DZ)
    cam.target = v1.pt(-DX * (COLS - 1) / 2, 0, -DZ)
    cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
    vp.camera = cam
    vp.fit()
    vp.saveAsImageFile(os.path.join(IMAGE_DIR, "overview.png"), 2400, 1500)


def run(context):
    ui = app.userInterface
    try:
        for doc in [app.documents.item(i) for i in range(app.documents.count)]:
            if doc.dataFile is None and doc.name.startswith("Untitled"):
                doc.close(False)
        app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(app.activeProduct)
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        looks = v2.looks_for()
        fa = v1.find_appearance
        looks["new"] = fa("plastic", "orange") or fa("orange") or fa("plastic", "yellow")
        looks["arrow"] = fa("plastic", "red") or fa("red")

        # the full v2 model is built first and hidden; the steps are copies of its parts
        v2.build(design, looks)
        built = [design.rootComponent.occurrences.item(i) for i in range(design.rootComponent.occurrences.count)]
        parts = collect_parts(design)
        steps = build_steps(design, looks, parts)
        for occ in built:
            occ.isLightBulbOn = False
        save_images(design, steps)

        # app.data.activeProject raises an internal validation error here, so there's no folder to pass to saveAs;
        # the save is left to the user.
        ui.messageBox("Assembly steps built: {} steps. Save with Cmd+S as '{}'.\nImages in {}".format(
            len(steps), DOC_NAME, IMAGE_DIR))
    except Exception:
        ui.messageBox("MemoryGlassesSteps failed:\n{}".format(traceback.format_exc()))
