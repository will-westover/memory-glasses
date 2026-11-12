"""Turns the active MemoryGlasses build into a "real life" look and queues photoreal renders.

Hides the helper geometry (reference eye, light paths, dowels, fit-test plate, antenna placeholder), makes every
printed part opaque glossy black and the lenses clear glass, then renders three views to
~/projects/memory-glasses/hardware/cad/renders/photoreal-*.png. Renders finish in Fusion's queue after the script exits.
"""

import os
import traceback

import adsk.core
import adsk.fusion

OUT_DIR = os.path.expanduser("~/projects/memory-glasses/hardware/cad/renders")
HIDE_COMPONENTS = ("Reference (not printed)", "Dowels (1.75mm filament)", "Fit-test plate (print first)")
HIDE_BODIES = ("Light path", "U.FL antenna")
VIEW_EXTENTS_CM = 13.0
# (name, eye, target, view extents in cm or None to fit) in mm; +X wearer's right, +Y forward, +Z up.
# The two close-ups look at where the camera housing and the HUD pod meet the frame.
VIEWS = [
    ("three-quarter", (230, 300, 110), (0, -30, 0), None),
    ("front", (0, 420, 25), (0, -20, 0), None),
    ("camera-side", (-380, 140, 70), (0, -60, 0), None),
    ("closeup-camera-joint", (-170, 160, 70), (-76, 0, 12), 4.0),
    ("closeup-pod-joint", (170, 170, 60), (72, 10, 10), 4.5),
]

app = adsk.core.Application.get()


def pt(x, y, z):
    return adsk.core.Point3D.create(x / 10.0, y / 10.0, z / 10.0)


def find_appearance(*keywords):
    lib = app.materialLibraries.itemByName("Fusion Appearance Library")
    for i in range(lib.appearances.count):
        a = lib.appearances.item(i)
        if all(k in a.name.lower() for k in keywords):
            return a
    return None


def all_bodies(root):
    for b in root.bRepBodies:
        yield b
    for occ in root.allOccurrences:
        for b in occ.component.bRepBodies:
            yield b


def run(context):
    ui = app.userInterface
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent
        # v2 builds tag themselves: they render matte and to their own file names.
        tag = design.attributes.itemByName("MemoryGlasses", "version")
        prefix = "photoreal-v{}-".format(tag.value) if tag else "photoreal-"
        finish = "matte" if tag else "glossy"
        black = find_appearance("plastic", finish, "black") or find_appearance("plastic", "black")
        glass = find_appearance("glass", "clear") or find_appearance("glass")

        for occ in root.allOccurrences:
            if occ.component.name in HIDE_COMPONENTS:
                occ.isLightBulbOn = False
        for b in all_bodies(root):
            if any(k in b.name for k in HIDE_BODIES):
                b.isLightBulbOn = False
            elif "(print)" in b.name:
                b.appearance = black
                b.opacity = 1.0
            elif "ThunderClap" in b.name:
                b.appearance = glass
                b.opacity = 1.0

        rm = design.renderManager
        try:
            rm.activateRenderWorkspace()
        except Exception:
            pass
        rendering = rm.rendering
        rendering.resolution = adsk.fusion.RenderResolutions.Mobile960x640RenderResolution

        vp = app.activeViewport
        os.makedirs(OUT_DIR, exist_ok=True)
        queued = []
        for name, eye, target, extents in VIEWS:
            cam = vp.camera
            cam.isSmoothTransition = False
            cam.eye = pt(*eye)
            cam.target = pt(*target)
            cam.upVector = adsk.core.Vector3D.create(0, 0, 1)
            if extents:
                cam.viewExtents = extents
                vp.camera = cam
            else:
                vp.camera = cam
                vp.fit()
                # fit() can leave the glasses small (it counts hidden helper geometry); the glasses are ~19cm
                # wide, so cap the view extents to keep them filling the frame.
                cam = vp.camera
                cam.isSmoothTransition = False
                cam.viewExtents = min(cam.viewExtents, VIEW_EXTENTS_CM)
                vp.camera = cam
            path =os.path.join(OUT_DIR, "{}{}.png".format(prefix, name))
            rendering.startLocalRender(path, vp.camera)
            queued.append(path)
        ui.messageBox("Queued {} renders:\n{}".format(len(queued), "\n".join(os.path.basename(p) for p in queued)))
    except Exception:
        ui.messageBox("MemoryGlassesShowroom failed:\n{}".format(traceback.format_exc()))
