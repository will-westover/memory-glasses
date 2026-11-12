"""Builds "Memory Glasses v2 - Soldering steps": a Fusion file showing every wire and solder joint in WIRING.md.

Each step is a component laid out on a flat bench, seen from the front: the XIAO with its back up and USB at the
top, the display, switch and battery. Earlier wires are faded, the new wire is solid, and the joints to make now are
orange. Wires are drawn short so everything fits; the real lengths are in step 1. Pictures of every step go to
~/projects/memory-glasses/hardware/cad/renders/soldering-steps/. Running this with the saved file open rebuilds inside it.
"""

import math
import os
import re
import sys
import traceback

import adsk.core
import adsk.fusion

FUSION_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
path = os.path.join(FUSION_DIR, "MemoryGlasses")
if path not in sys.path:
    sys.path.insert(0, path)
import MemoryGlasses as v1  # noqa: E402

tbm, app, P = v1.tbm, v1.app, v1.P

DOC_NAME = "Memory Glasses v2 - Soldering steps"
IMAGE_DIR = os.path.expanduser("~/projects/memory-glasses/hardware/cad/renders/soldering-steps")
COLS, DX, DZ = 4, 170.0, 135.0
VIEW_EXTENTS_CM, CLOSEUP_EXTENTS_CM = 10.6, 3.6
COLORS = {
    "pcb": (40, 115, 65), "display": (30, 35, 90), "switch": (25, 25, 25), "metal": (190, 195, 200), "pad": (212, 175, 55),
    "now": (255, 120, 0), "red": (220, 30, 30), "black": (20, 20, 20), "yellow": (240, 200, 0), "green": (30, 170, 70),
    "white": (245, 245, 245), "blue": (30, 110, 240),
}
# The midpoint of the castellated pads, in the vendor model's coordinates (measured from its faces).
PAD_CENTRE_X, PAD_CENTRE_Z = 1.74, -6.115

BODY_LOOKS = {
    "XIAO part": "pcb", "Display board": "display", "Display pin": "metal", "Switch": "switch", "Switch pin": "metal",
    "Battery 601452": "metal", "Pad": "pad", "Solder joint (done)": "metal", "SOLDER HERE": "now",
}

# All layout positions are (sx, sz): millimetres right and up on screen from the XIAO's centre, seen from the front.
# XIAO pads are measured from the vendor model; which pad is which comes from Seeed's pinout.
PADS = {
    "GND": (-7.95, 5.08), "3V3": (-7.95, 2.54), "D4": (7.95, -2.54), "D5": (7.95, -5.08),
    "BAT-": (4.45, 2.29), "BAT+": (4.45, 0.38),
}
OLED = {"GND": (36.0, 13.81), "VCC": (36.0, 11.27), "SCL": (36.0, 8.73), "SDA": (36.0, 6.19)}
SWITCH_MID, SWITCH_OUT = (30.3, -27.0), (32.84, -27.0)
BAT_RED, BAT_BLACK = (42.0, -35.0), (42.0, -45.0)
SPLICE = (36.0, -45.0)

# colour, height above the board (so crossings stay apart), route
WIRES = {
    "red": ("red", 2.2, [PADS["3V3"], (-13.0, 2.54), (-13.0, 20.0), (30.0, 20.0), (30.0, 11.27), OLED["VCC"]]),
    "black": ("black", 1.8, [PADS["GND"], (-11.0, 5.08), (-11.0, 18.0), (28.0, 18.0), (28.0, 13.81), OLED["GND"]]),
    "yellow": ("yellow", 1.0, [PADS["D4"], (24.0, -2.54), (24.0, 6.19), OLED["SDA"]]),
    "green": ("green", 1.4, [PADS["D5"], (20.0, -5.08), (20.0, 8.73), OLED["SCL"]]),
    "white": ("white", 2.6, [PADS["BAT+"], (5.6, 0.38), (5.6, -14.0), (30.3, -14.0), SWITCH_MID]),
    "blue": ("blue", 3.0, [PADS["BAT-"], (2.6, 2.29), (2.6, -20.0), (22.0, -20.0), (22.0, -45.0), SPLICE]),
    "lead_red": ("red", 3.4, [BAT_RED, (40.0, -35.0), (40.0, -24.0), (32.84, -24.0), SWITCH_OUT]),
    "lead_black": ("black", 3.4, [BAT_BLACK, SPLICE]),
}

# title, instruction, wires new in this step, joints to make now
STEPS = [
    ("Set up", "Back of the XIAO up, USB at the top. USB unplugged, battery leads kept apart. Iron about 350 C.\n"
     "Cut wires: 4 at 230 mm (red, black, yellow, green), 2 at 260 mm (white, blue). "
     "Strip 2 mm off each end, twist, and coat with solder. Practice on scrap first.", [], []),
    ("Red: 3V3", "Red wire to the XIAO's 3V3 pad: left side, 3rd pad down. Other end comes later.",
     ["red"], [PADS["3V3"]]),
    ("Black: GND", "Black wire to the XIAO's GND pad: left side, 2nd pad down.", ["black"], [PADS["GND"]]),
    ("Yellow: D4", "Yellow wire to D4: right side, 5th pad down.", ["yellow"], [PADS["D4"]]),
    ("Green: D5", "Green wire to D5: right side, 6th pad down. Check it hasn't bridged to D4.", ["green"], [PADS["D5"]]),
    ("White: BAT+", "White wire to the BAT+ pad: the small center pad farther from USB.", ["white"], [PADS["BAT+"]]),
    ("Blue: BAT-", "Blue wire to the BAT- pad: the small center pad closer to USB. Check for a bridge to BAT+.",
     ["blue"], [PADS["BAT-"]]),
    ("Display", "Red to VCC, black to GND, yellow to SDA, green to SCL. GO BY THE LABELS ON YOUR BOARD: pin order varies.\n"
     "If it has pins already, clip them to about 1 mm and solder to those.",
     [], [OLED["VCC"], OLED["GND"], OLED["SDA"], OLED["SCL"]]),
    ("Switch", "White wire to the switch's MIDDLE pin.", [], [SWITCH_MID]),
    ("Battery, last", "If it has a connector, cut leads ONE AT A TIME so they can't touch. Red lead to a switch OUTER pin "
     "(add a short wire if it won't reach). Black lead to the blue wire. Keep the iron off the cell. Tape each joint.",
     ["lead_red", "lead_black"], [SWITCH_OUT, SPLICE]),
    ("Check, then test", "Look for bridges. Multimeter: no short between 3V3 and GND, or BAT+ and BAT-.\n"
     "Switch off, plug in USB, upload firmware/display_test. Expect white, then a border, then a counter.", [], []),
]

BOARD_LABELS = [
    ("GND", -6.8, 5.9), ("3V3", -6.8, 3.36), ("BAT-", -2.6, 3.1), ("BAT+", -2.6, 1.2),
    ("D4", 9.2, -0.7), ("D5", 9.2, -3.25),
    ("GND", 38.0, 14.6), ("VCC", 38.0, 12.1), ("SCL", 38.0, 9.5), ("SDA", 38.0, 7.0),
]
PART_LABELS = [
    ("USB at this end", -9.0, 17.0, 22.0, 2.5, 1.8),
    ("XIAO, back up", -15.0, -12.0, 17.0, 2.5, 1.8),
    ("Display (your pin order may differ)", 34.0, -5.0, 40.0, 3.0, 2.0),
    ("Switch: middle,\nouter pins", 24.0, -32.0, 15.5, 5.5, 1.8),
    ("Battery: red +, black -", 44.0, -48.5, 50.0, 3.0, 2.0),
]


def world(T, s, y):
    """Screen layout position to model space: seen from the front, model +X is on the viewer's left."""
    return (T[0] - s[0], y, T[2] + s[1])


def box_s(T, s0, s1, y0, y1):
    a, b = world(T, s0, y0), world(T, s1, y1)
    return v1.box_span(min(a[0], b[0]), max(a[0], b[0]), y0, y1, min(a[2], b[2]), max(a[2], b[2]))


def import_xiao(root):
    """Imports the vendor model where it is: moving the occurrence leaves its main board behind."""
    before = root.occurrences.count
    opts = app.importManager.createSTEPImportOptions(os.path.expanduser(P["xiao_step"]))
    app.importManager.importToTarget(opts, root)
    if root.occurrences.count == before:
        raise RuntimeError("XIAO model didn't import")
    return root.occurrences.item(root.occurrences.count - 1)


def xiao_bodies(root):
    found = []
    for occ in root.allOccurrences:
        if "ESP32" in occ.fullPathName:
            found += [b for b in occ.bRepBodies]
    return found


def on_bench(body):
    """A copy with its back up (+Y), USB toward +Z (model +x) and the pads centred on x=z=0."""
    t = tbm.copy(body)
    m = adsk.core.Matrix3D.create()
    m.setWithCoordinateSystem(v1.pt(-PAD_CENTRE_Z, 0, -PAD_CENTRE_X), adsk.core.Vector3D.create(0, 0, 1),
                              adsk.core.Vector3D.create(0, -1, 0), adsk.core.Vector3D.create(1, 0, 0))
    tbm.transform(t, m)
    return t


def moved(t, d):
    m = adsk.core.Matrix3D.create()
    m.translation = adsk.core.Vector3D.create(v1.cm(d[0]), v1.cm(d[1]), v1.cm(d[2]))
    tbm.transform(t, m)
    return t


def union(body, other):
    tbm.booleanOperation(body, other, adsk.fusion.BooleanTypes.UnionBooleanType)


def wire_body(T, key, y_back, joined):
    """joined: its far end is soldered. Until then the wire stops short of it, so it doesn't read as connected."""
    _, height, route = WIRES[key]
    if not joined:
        (ax, az), (bx, bz) = route[-2], route[-1]
        route = route[:-1] + [((ax + bx) / 2, (az + bz) / 2)]
    y = y_back + height
    pts = [world(T, s, y) for s in route]
    # the soldered ends drop down onto their pads
    body = v1.cyl(world(T, route[0], y_back), pts[0], 0.45)
    for a, b in zip(pts, pts[1:]):
        union(body, v1.cyl(a, b, 0.45))
        union(body, tbm.createSphere(v1.pt(*b), v1.cm(0.45)))
    if joined:
        union(body, v1.cyl(pts[-1], world(T, route[-1], y_back), 0.45))
    return body


def add_text(sk, y, text, T, s_left, s_top, width, height, size):
    x0 = T[0] - (s_left + width)
    ti = sk.sketchTexts.createInput2(text, v1.cm(size))
    c1 = sk.modelToSketchSpace(v1.pt(x0, y, T[2] + s_top))
    c2 = sk.modelToSketchSpace(v1.pt(x0 + width, y, T[2] + s_top - height))
    # rounding in the offset plane's position leaves these slightly off it, which setAsMultiLine rejects
    c1.z = 0.0
    c2.z = 0.0
    ti.setAsMultiLine(c1, c2, adsk.core.HorizontalAlignments.LeftHorizontalAlignment,
                      adsk.core.VerticalAlignments.TopVerticalAlignment, 0)
    # the XZ plane's sketch axes run opposite to model X and Z, so unrotated text reads upside down from the front
    ti.angle = math.pi
    sk.sketchTexts.add(ti)


def add_labels(comp, T, i, title, detail, y_back):
    y = y_back + 7.0
    sk = comp.sketches.add(v1.plane_at_y(comp, y))
    sk.name = "Labels"
    for text, sx, sz in BOARD_LABELS:
        add_text(sk, y, text, T, sx, sz, 6.0, 2.0, 1.5)
    for text, sx, sz, width, height, size in PART_LABELS:
        add_text(sk, y, text, T, sx, sz, width, height, size)
    add_text(sk, y, "STEP {} of {}: {}".format(i + 1, len(STEPS), title.upper()), T, -15.0, 47.0, 125.0, 6.0, 4.5)
    add_text(sk, y, detail, T, -15.0, 40.0, 125.0, 16.0, 2.6)


def build_steps(design, xiao, y_back):
    root = design.rootComponent
    done_wires, done_joints, steps = [], [], []
    for i, (title, detail, new_wires, joints) in enumerate(STEPS):
        col, row = i % COLS, i // COLS
        T = (-col * DX, 0.0, -row * DZ)
        name = "Step {:02d} - {}".format(i + 1, title)
        comp = v1.new_component(root, name)
        entries = [(moved(on_bench(b), T), "XIAO part") for b in xiao]

        entries.append((box_s(T, (34.0, -3.5), (61.0, 23.5), y_back - 1.6, y_back), "Display board"))
        for label, s in OLED.items():
            entries.append((v1.cyl(world(T, s, y_back - 1.6), world(T, s, y_back + 0.2), 0.45), "Display pin " + label))
        entries.append((box_s(T, (26.0, -31.0), (34.6, -28.0), y_back - 3.5, y_back), "Switch"))
        for sx in (27.76, 30.3, 32.84):
            entries.append((box_s(T, (sx - 0.35, -28.0), (sx + 0.35, -26.5), y_back - 1.5, y_back - 1.0), "Switch pin"))
        entries.append((box_s(T, (42.0, -47.0), (94.0, -33.0), y_back - 6.0, y_back), "Battery 601452"))

        for s in PADS.values():
            entries.append((v1.cyl(world(T, s, y_back), world(T, s, y_back + 0.1), 0.6), "Pad"))

        for key in done_wires + new_wires:
            far_end = WIRES[key][2][-1]
            active = key in new_wires or far_end in joints
            body = wire_body(T, key, y_back, far_end in done_joints + joints)
            entries.append((body, "Wire ({}): {}".format("now" if active else "done", key)))
        for s in done_joints:
            entries.append((tbm.createSphere(v1.pt(*world(T, s, y_back + 0.2)), v1.cm(0.6)), "Solder joint (done)"))
        for s in joints:
            entries.append((tbm.createSphere(v1.pt(*world(T, s, y_back + 0.4)), v1.cm(0.85)), "SOLDER HERE"))

        bf = comp.features.baseFeatures.add()
        bf.startEdit()
        for t, _ in entries:
            comp.bRepBodies.add(t, bf)
        bf.finishEdit()
        for k, (_, label) in enumerate(entries):
            bf.bodies.item(k).name = label
        add_labels(comp, T, i, title, detail, y_back)

        done_wires += new_wires
        done_joints += joints
        steps.append((name, T))
    return steps


def view(vp, T, centre, extents):
    cam = vp.camera
    cam.isSmoothTransition = False
    target = (T[0] - centre[0], 0.0, T[2] + centre[1])
    cam.target = v1.pt(*target)
    cam.eye = v1.pt(target[0], 500.0, target[2])
    cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
    cam.viewExtents = extents
    vp.camera = cam
    vp.refresh()


def save_images(design, steps):
    os.makedirs(IMAGE_DIR, exist_ok=True)
    vp = app.activeViewport
    occs = {o.component.name: o for o in design.rootComponent.occurrences if o.component.name.startswith("Step ")}
    for (name, T), (_, _, _, joints) in zip(steps, STEPS):
        for other, o in occs.items():
            o.isLightBulbOn = other == name
        stem = os.path.join(IMAGE_DIR, name.split(" - ")[0].lower().replace(" ", "-"))
        view(vp, T, (47.0, -2.25), VIEW_EXTENTS_CM)
        vp.saveAsImageFile(stem + ".png", 1800, 1200)
        if any(s in PADS.values() for s in joints):
            view(vp, T, (0.0, 2.0), CLOSEUP_EXTENTS_CM)
            vp.saveAsImageFile(stem + "-closeup.png", 1800, 1200)
    # Left all showing and aimed at step 1, so panning right walks through the steps.
    for o in occs.values():
        o.isLightBulbOn = True
    view(vp, steps[0][1], (47.0, -2.25), VIEW_EXTENTS_CM)


def solid_looks(design):
    """Named colours: the appearance library has no orange, and its "aluminum" renders blue."""
    base = v1.find_appearance("plastic", "white")
    looks = {}
    for name, (r, g, b) in COLORS.items():
        look = design.appearances.itemByName("Solder " + name) or design.appearances.addByCopy(base, "Solder " + name)
        prop = adsk.core.ColorProperty.cast(look.appearanceProperties.itemById("opaque_albedo"))
        prop.value = adsk.core.Color.create(r, g, b, 255)
        looks[name] = look
    return looks


def look_key(label):
    # Fusion numbers repeated body names: "XIAO part (1)"
    label = re.sub(r" \(\d+\)$", "", label)
    if label.startswith("Wire"):
        return WIRES[label.split(": ")[1]][0]
    return BODY_LOOKS[label.split(" pin")[0] + " pin" if " pin" in label else label]


def paint(design):
    """Runs after the build: appearances made before it are gone by the end, and the bodies silently stay steel."""
    looks = solid_looks(design)
    for occ in design.rootComponent.occurrences:
        if not occ.component.name.startswith("Step "):
            continue
        for body in occ.component.bRepBodies:
            body.appearance = looks[look_key(body.name)]
            body.opacity = 0.7 if body.name.startswith("Wire (done)") else 1.0


def run(context):
    ui = app.userInterface
    try:
        active = app.activeDocument
        rebuilding = active is not None and active.dataFile is not None and active.name.startswith(DOC_NAME)
        folder = None
        if rebuilding:
            design = adsk.fusion.Design.cast(app.activeProduct)
            design.timeline.moveToBeginning()
            design.timeline.deleteAllAfterMarker()
            root = design.rootComponent
            for occ in [root.occurrences.item(i) for i in range(root.occurrences.count)]:
                occ.deleteMe()
        else:
            # app.data.activeProject raises in this setup, so find the project through an already-saved file.
            for i in range(app.documents.count):
                df = app.documents.item(i).dataFile
                if df is not None:
                    folder = df.parentFolder
            for doc in [app.documents.item(i) for i in range(app.documents.count)]:
                if doc.dataFile is None and doc.name.startswith("Untitled"):
                    doc.close(False)
            app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            design = adsk.fusion.Design.cast(app.activeProduct)
            design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        doc = app.activeDocument
        root = design.rootComponent

        source = import_xiao(root)
        xiao = xiao_bodies(root)
        placed = [on_bench(b).boundingBox.maxPoint.y for b in xiao]
        y_back = max(placed) * 10
        steps = build_steps(design, xiao, y_back)
        source.isLightBulbOn = False
        paint(design)
        save_images(design, steps)

        status = "Save with Cmd+S as '{}'.".format(DOC_NAME)
        try:
            if rebuilding:
                if doc.save("Rebuilt the soldering steps on the XIAO's measured pads"):
                    status = "Saved a new version."
            elif folder is not None and doc.saveAs(DOC_NAME, folder, "Every wire and solder joint for Memory Glasses v2", ""):
                status = "Saved as '{}'.".format(DOC_NAME)
        except Exception as e:
            status = "Couldn't save automatically ({}). Save with Cmd+S.".format(e)
        ui.messageBox("Soldering steps built: {} steps, {} XIAO parts per step, back at y={:.2f}.\n{}".format(
            len(steps), len(xiao), y_back, status))
    except Exception:
        ui.messageBox("MemoryGlassesSolder failed:\n{}".format(traceback.format_exc()))
