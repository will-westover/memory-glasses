"""Builds Memory Glasses v2: the same parts and internals as v1, restyled toward the "integrated" concept image
(flat brow, modules flush with the brow top, recessed camera ring, tinted pod window).

The front, the camera housing and the optics pod's housing are one print ("Front unit"). Their outer shells are
joined first and rounded in a single fillet, so every place a module meets the frame gets the same smooth blend;
only then are the cavities, lens seats, lid slots and wire channels cut. Parts that open or move stay separate:
caps, lids, the display sled, spacers, arms. Print the front unit standing as worn: both modules open at the top,
so their insides stay clear (supports and brim: see hardware/cad/print/v2/README.md).

Fits verified in v1 (XIAO cavity, optics stations, lid and cap slides, battery pocket) carry over unchanged.
Axes as in v1: +X wearer's right, +Y forward, +Z up (mm).
"""

import importlib
import os
import re
import sys
import traceback

import adsk.core
import adsk.fusion

V1_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "MemoryGlasses")
if V1_DIR not in sys.path:
    sys.path.insert(0, V1_DIR)
import MemoryGlasses as v1  # noqa: E402

importlib.reload(v1)
P = v1.P
box_span, box, cyl, solid, cut_from = v1.box_span, v1.box, v1.cyl, v1.solid, v1.cut_from
tbm, app = v1.tbm, v1.app

V2 = {
    # flat brow: level with the pod's top (ccz + half + wall) so the brow and both modules share one top plane
    "brow_top": 28.1,
    # one fillet over the joined shells: module edges and module-to-frame blends, the frame's own edges, and the
    # top plane. Lid and cap tongue grooves run 0.75 under the top face, 0.75 inside the wall, so top edges stay
    # at 1.5 (rounder cut into the grooves); 2.5 elsewhere leaves 1.0mm at the cube cage's corners.
    "module_fillet": 2.5,
    "top_fillet": 1.5,
    # ring recessed around the camera window
    "lens_recess": (5.5, 0.6),
    # tinted window on the pod's front face (cosmetic; a printed dark insert or tinted acrylic)
    "pod_window_inset": 0.6,
}

# v1's traced outline below the brow, with the top flattened to brow_top and a shallow dip at the bridge.
FRONT_RIGHT_V2 = [
    (0, 23.0), (7.3, 23.6), (14.5, 25.2), (21.7, 26.6), (29.0, 27.4), (36.2, 27.8), (43.4, 28.1),
    (50.6, 28.1), (57.9, 28.1), (65.1, 28.1), (72.3, 28.1), (74.6, 27.4), (75.3, 24.0), (75.2, 19.5),
] + v1.FRONT_RIGHT[13:]
FRONT = "Front (print)"
FRONT_UNIT = "Front unit: frame + camera housing + pod housing (print)"


def camera_layout():
    """The v1 camera block's stations, with the top raised to the flat brow."""
    w, c = P["wall"], P["clear"]
    xl, xw, xs = P["xiao_len"], P["xiao_w"], P["xiao_stack"]
    L = {"cap_t": 2.0, "cap_tongue": 0.6}
    L["x_inner"] = -(P["temple_x"] + 2.0)
    L["x_outer"] = L["x_inner"] - (xw + 2 * c + 2 * w + P["ant_gap"])
    L["y_front"] = -P["front_t"] / 2
    L["y_back"] = L["y_front"] - (w + xs + 2 * c + P["block_back_wall"])
    L["cz"] = P["xiao_cz"]
    L["z0"], L["z1"] = L["cz"] - xl / 2 - c - w, V2["brow_top"]
    L["bx"] = L["x_inner"] - w - c - xw / 2
    L["stack_front"] = L["y_front"] - w - c
    L["stack_back"] = L["stack_front"] - xs
    L["by"] = (L["stack_front"] + L["stack_back"]) / 2
    L["cam_x"], L["cam_z"] = L["bx"] - P["cam_across"], L["cz"] - P["cam_along"]
    L["bottom"] = L["cz"] - xl / 2
    return L


def pod_layout():
    """The v1 optics pod's stations (cube, lens, display travel) along its axis."""
    w, s = P["wall"], P["cube"]
    ex, ey, ez = P["eye"]
    inner = P["oled_pcb"] + 2 * P["oled_shim"]
    L = {"half": inner / 2, "ccx": ex + P["cube_dx"], "ccz": ez + P["cube_dz"]}
    L["ccy"] = P["front_t"] / 2 + w + inner / 2
    L["lens_x0"], L["lens_x1"], L["lens_mid"], L["slide_end"] = v1.pod_stations()
    L["oled_x0"] = L["lens_mid"] + P["lens_f"] * P["oled_gap_ratio"]
    L["oled_x1"] = L["oled_x0"] + P["oled_t"]
    L["top"] = L["ccz"] + L["half"] + w
    return L


def camera_shell(L):
    return box_span(L["x_outer"], L["x_inner"], L["y_back"], L["y_front"], L["z0"], L["z1"])


def pod_shell(L):
    w, s, half = P["wall"], P["cube"], L["half"]
    cage = box(L["ccx"], L["ccy"], L["ccz"], s + 2 * w, s + 2 * w, s + 2 * w)
    tunnel = box_span(L["lens_x0"] - 1, L["slide_end"] + w, L["ccy"] - half - w, L["ccy"] + half + w,
                      L["ccz"] - half - w, L["ccz"] + half + w)
    tbm.booleanOperation(cage, tunnel, adsk.fusion.BooleanTypes.UnionBooleanType)
    return cage


def build_front_unit(comp, looks, cam, pod):
    """Front outline + both module shells as one solid, rounded in one fillet before anything is cut into it."""
    lcx, lcz = v1.lens_centre()
    sk = comp.sketches.add(comp.xZConstructionPlane)
    sk.name = "Front outline"
    v1.closed_spline(sk, [(x, 0, z) for x, z in v1.mirrored_loop(FRONT_RIGHT_V2)])
    for s in (-1, 1):
        lens = v1.closed_spline(sk, [(s * x, 0, z) for x, z in v1.LENS_RIGHT])
        v1.offset_curve(sk, lens, (s * lcx, 0, lcz), P["lip_inset"])
    profs = adsk.core.ObjectCollection.create()
    for p in sk.profiles:
        if p.profileLoops.count >= 2:
            profs.add(p)
    ei = comp.features.extrudeFeatures.createInput(profs, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei.setSymmetricExtent(v1.val(P["front_t"]), True)
    comp.features.extrudeFeatures.add(ei).bodies.item(0).name = FRONT
    v1.join_to(comp, FRONT, [camera_shell(cam), pod_shell(pod)])
    return round_front_unit(comp, cam, pod)


def round_front_unit(comp, cam, pod):
    """Rounds the joined shells with three radii: module edges (including where they meet the frame), top-plane
    edges, and the frame's own edges. Returns a short report of what got rounded."""
    ff, top = P["front_t"] / 2, V2["brow_top"]
    k = 0.5
    cam_box = (cam["x_outer"] - k, cam["x_inner"] + k, cam["y_back"] - k, cam["y_front"] + k, cam["z0"] - k, top + k)
    pod_box = (pod["ccx"] - P["cube"] / 2 - P["wall"] - k, pod["slide_end"] + P["wall"] + k, ff + 0.05,
               pod["ccy"] + pod["half"] + P["wall"] + k, pod["ccz"] - pod["half"] - P["wall"] - k, top + k)
    # the pod's blends sit on the frame's face (y = ff); the lens openings' front edges share that plane, so only
    # take face edges outboard of the lens (x >= 62) and within the tunnel's height
    seam_x, seam_z = 62.0, (pod["ccz"] - pod["half"] - P["wall"] - k, top + k)

    def inside(p, b):
        return b[0] <= p[0] <= b[1] and b[2] <= p[1] <= b[3] and b[4] <= p[2] <= b[5]

    def classify(e):
        q = e.pointOnEdge
        p = (q.x * 10, q.y * 10, q.z * 10)
        if p[2] >= top - 0.05:
            return "top"
        on_pod_seam = abs(p[1] - ff) <= 0.05 and p[0] >= seam_x and seam_z[0] <= p[2] <= seam_z[1]
        if inside(p, cam_box) or inside(p, pod_box) or on_pod_seam:
            return "module"
        return "frame"

    def fillet(edges, r):
        col = adsk.core.ObjectCollection.create()
        for e in edges:
            col.add(e)
        fi = comp.features.filletFeatures.createInput()
        try:
            fi.edgeSetInputs.addConstantRadiusEdgeSet(col, v1.val(r), True)
        except AttributeError:
            fi.addConstantRadiusEdgeSet(col, v1.val(r), True)
        try:
            comp.features.filletFeatures.add(fi)
            return True
        except Exception:
            return False

    def key(e):
        q = e.pointOnEdge
        return round(q.x, 3), round(q.y, 3), round(q.z, 3)

    # Module edges and blends first (the look depends on them), then the top plane, then the frame. A set Fusion
    # rejects as a whole is redone one edge at a time, so one awkward edge only loses its own rounding.
    radii = {"module": V2["module_fillet"], "top": V2["top_fillet"], "frame": P["front_fillet"]}
    report = []
    for name in ("module", "top", "frame"):
        r = radii[name]
        edges = [e for e in comp.bRepBodies.itemByName(FRONT).edges if classify(e) == name]
        if not edges:
            continue
        if fillet(edges, r):
            report.append("{} all {}".format(name, len(edges)))
            continue
        tried, ok = set(), 0
        for _ in range(250):
            cands = [e for e in comp.bRepBodies.itemByName(FRONT).edges if classify(e) == name and key(e) not in tried]
            if not cands:
                break
            e = cands[0]
            tried.add(key(e))
            if fillet([e], r) or fillet([e], r / 2):
                ok += 1
        report.append("{} {} one by one".format(name, ok))
    return ", ".join(report)


def add_lens_seats(comp, looks):
    """v1's lens seats, curved ThunderClap lenses and press-fit retainer tabs, cut into the rounded front unit."""
    lcx, lcz = v1.lens_centre()
    ff, fb = P["front_t"] / 2, -P["front_t"] / 2
    front = comp.bRepBodies.itemByName(FRONT)
    seat_depth = P["front_t"] - P["lip"]
    seat = comp.sketches.add(v1.plane_at_y(comp, fb))
    seat.name = "Lens seats"
    for s in (-1, 1):
        lens = v1.closed_spline(seat, [(s * x, fb, z) for x, z in v1.LENS_RIGHT])
        v1.offset_curve(seat, lens, (s * lcx, fb, 60.0), P["lens_clear"])
    si = comp.features.extrudeFeatures.createInput(v1.all_profiles(seat), adsk.fusion.FeatureOperations.CutFeatureOperation)
    si.setSymmetricExtent(v1.val(2 * seat_depth), True)
    si.participantBodies = [front]
    comp.features.extrudeFeatures.add(si)

    r, ct = P["lens_base_r"], P["lens_ct"]
    half_h = v1.min_radius(v1.LENS_RIGHT, lcx, lcz) - P["lip_inset"]
    sag = r - (r * r - half_h * half_h) ** 0.5
    y_centre = (ff - P["lip"]) + sag
    for s, label in ((-1, "Left lens (ThunderClap)"), (1, "Right lens (ThunderClap)")):
        lsk = comp.sketches.add(comp.xZConstructionPlane)
        lsk.name = label
        v1.closed_spline(lsk, [(s * x, 0, z) for x, z in v1.LENS_RIGHT])
        li = comp.features.extrudeFeatures.createInput(lsk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        li.setSymmetricExtent(v1.val(40.0), True)
        comp.features.extrudeFeatures.add(li).bodies.item(0).name = label
        centre = (s * lcx, y_centre - r, lcz)
        shell = tbm.createSphere(v1.pt(*centre), v1.cm(r))
        tbm.booleanOperation(shell, tbm.createSphere(v1.pt(*centre), v1.cm(r - ct)), adsk.fusion.BooleanTypes.DifferenceBooleanType)
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
        v1.style(comp.bRepBodies.itemByName(label), looks["glass"], 0.25)
    v1.add_lens_retainers(comp, looks, lcx, lcz, y_centre - r)


def finish_camera(root, frame, looks, L):
    """Cuts the XIAO cavity, ports, window, cap slide and the left arm's T-slot into the front unit; adds the cap,
    the board and the antenna."""
    comp = v1.new_component(root, "Left hinge block: camera + XIAO")
    w, c, sc = P["wall"], P["clear"], P["slide_clear"]
    cap_t, tongue = L["cap_t"], L["cap_tongue"]
    xl = P["xiao_len"]
    yf, z0, z1, bx, by = L["y_front"], L["z0"], L["z1"], L["bx"], L["by"]
    cav0, cav1 = L["stack_back"] - c, L["stack_front"] + c
    r_rec, d_rec = V2["lens_recess"]
    cut_from(frame, FRONT, [
        box_span(L["x_outer"] + w, L["x_inner"] - w, cav0, cav1, L["bottom"] - c, z1 - cap_t),
        cyl((L["cam_x"], yf - w - 0.5, L["cam_z"]), (L["cam_x"], yf + 0.01, L["cam_z"]), P["cam_win"] / 2),
        cyl((L["cam_x"], yf - d_rec, L["cam_z"]), (L["cam_x"], yf + 0.01, L["cam_z"]), r_rec),
        box_span(bx + P["port_w0"], bx + P["port_w1"], L["stack_back"] - c, L["stack_back"] + P["port_depth"],
                 z0 - 0.5, L["bottom"] + 0.1),
        # a USB-C cable's moulded head (~12.4 x 6.5) has to reach the port 3.4mm inside the block
        box(bx, by + P["usb_dy"], (z0 + L["bottom"]) / 2, P["usb_plug"][0], P["usb_plug"][1], L["bottom"] - z0 + 1.2),
        # pencil-box cap: open top, tongue grooves in the front/back walls, entry through the outer wall
        box_span(L["x_outer"] - 1, L["x_inner"] - w, cav0, cav1, z1 - cap_t - sc, z1 + 1),
        box_span(L["x_outer"] - 1, L["x_inner"] - w, cav0 - tongue - sc, cav1 + tongue + sc,
                 z1 - cap_t - sc, z1 - cap_t / 2 + sc),
    ] + v1.t_slot(P["rail_left"][0], L["y_back"], P["rail_left"][1], sc))
    cap = box_span(L["x_outer"], L["x_inner"] - w - sc, cav0 + sc, cav1 - sc, z1 - cap_t, z1)
    tbm.booleanOperation(cap, box_span(L["x_outer"], L["x_inner"] - w - sc, cav0 - tongue, cav1 + tongue,
                                       z1 - cap_t, z1 - cap_t / 2),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    solid(comp, "Camera block sliding cap (print)", cap, looks["frame"], 0.55)
    v1.place_step(comp, P["xiao_step"], P["xiao_step_centre"], (bx, by, L["cz"]))
    solid(comp, "U.FL antenna (size unverified)", box_span(L["x_outer"] + w, L["x_outer"] + w + 0.8, by - 6, by + 6,
                                                            L["cz"] - 8, L["cz"] + 8), looks["lensblack"])


def finish_pod(root, frame, looks, L):
    """v1.build_optics_pod's cavities, lid, lift-off cap, sled, spacers and optics, cut into / built around the
    front unit instead of a separate housing."""
    comp = v1.new_component(root, "Optics pod (HUD)")
    w, s, sc = P["wall"], P["cube"], P["slide_clear"]
    ccx, ccy, ccz, half = L["ccx"], L["ccy"], L["ccz"], L["half"]
    lens_x0, lens_x1, lens_mid, slide_end = L["lens_x0"], L["lens_x1"], L["lens_mid"], L["slide_end"]
    oled_x0, oled_x1, top = L["oled_x0"], L["oled_x1"], L["top"]
    ff = P["front_t"] / 2
    window = s - 3.0
    cut_from(frame, FRONT, [
        box(ccx, ccy, ccz, s + 0.3, s + 0.3, s + 0.3),
        cyl((lens_x0 - 0.2, ccy, ccz), (lens_x1 + 0.1, ccy, ccz), P["lens_d"] / 2 + 0.15),
        box_span(lens_x1, slide_end, ccy - half, ccy + half, ccz - half, ccz + half),
        box(ccx, ccy, ccz, window, s + 4 * w, window),
        box_span(ccx, lens_x0 + 0.5, ccy - s / 2 + 1, ccy + s / 2 - 1, ccz - s / 2 + 1, ccz + s / 2 - 1),
    ])

    # Tunnel lid: pencil-box plate that slides in through the far end wall and stops against the lens block.
    lid_t, lid_tongue = 2.0, 0.6
    end_x = slide_end + w
    cut_from(frame, FRONT, [
        box_span(lens_x1, end_x + 1, ccy - half, ccy + half, top - lid_t - sc, top + 1),
        box_span(lens_x1, end_x + 1, ccy - half - lid_tongue - sc, ccy + half + lid_tongue + sc,
                 top - lid_t - sc, top - lid_t / 2 + sc),
    ])
    lid = box_span(lens_x1 + sc, end_x, ccy - half + sc, ccy + half - sc, top - lid_t, top)
    tbm.booleanOperation(lid, box_span(lens_x1 + sc, end_x, ccy - half - lid_tongue, ccy + half + lid_tongue,
                                       top - lid_t, top - lid_t / 2),
                         adsk.fusion.BooleanTypes.UnionBooleanType)
    solid(comp, "Optics tunnel sliding lid (print)", lid, looks["frame"], 0.35)

    # Cube + lens cap lifts off on two friction dowels. It starts at the frame's face (y = ff) so it takes the
    # pod's top half and its blend, but none of the frame.
    cap_region = box_span(ccx - s, lens_x1, ff, ccy + half + 2 * w, ccz, top + 1)
    cap = tbm.copy(frame.bRepBodies.itemByName(FRONT))
    tbm.booleanOperation(cap, cap_region, adsk.fusion.BooleanTypes.IntersectionBooleanType)
    cut_from(frame, FRONT, [tbm.copy(cap_region)])
    pin_x = (lens_x0 + lens_x1) / 2
    cap_pins = [(pin_x, ccy - 14.0), (pin_x, ccy + 14.0)]
    for x, y in cap_pins:
        tbm.booleanOperation(cap, cyl((x, y, ccz - 0.1), (x, y, ccz + 3.2), P["hole_r"]),
                             adsk.fusion.BooleanTypes.DifferenceBooleanType)
    cut_from(frame, FRONT, [cyl((x, y, ccz + 0.1), (x, y, ccz - 3.2), P["hole_r"]) for x, y in cap_pins])
    solid(comp, "Optics cube + lens cap (print)", cap, looks["frame"], 0.35)
    for x, y in cap_pins:
        solid(comp, "Cap friction dowel", cyl((x, y, ccz - 3.0), (x, y, ccz + 3.0), P["pin_r"]), looks["pin"])

    sled_wall, lip = 1.2, P["sled_lip"]
    so = P["oled_pcb"] + 2 * sled_wall
    pcb, c = P["oled_pcb"], P["clear"]
    sled = box_span(oled_x0 - lip, oled_x1, ccy - so / 2, ccy + so / 2, ccz - so / 2, ccz + so / 2)
    tbm.booleanOperation(sled, box_span(oled_x0 - 0.01, oled_x1 + 1, ccy - pcb / 2 - c, ccy + pcb / 2 + c,
                                        ccz - pcb / 2 - c, ccz + pcb / 2 + c),
                         adsk.fusion.BooleanTypes.DifferenceBooleanType)
    tbm.booleanOperation(sled, box(oled_x0 - lip / 2, ccy, ccz, lip + 0.2, P["oled_window"][0], P["oled_window"][1]),
                         adsk.fusion.BooleanTypes.DifferenceBooleanType)
    solid(comp, "Display sled (print)", sled, looks["frame"], 0.35)

    front_len, rear_len = v1.focus_spacer_lengths(P["oled_gap_ratio"], lens_mid, lens_x1, slide_end)
    rod = P["spacer_rod"]
    for sy in (-1, 1):
        ry = ccy + sy * (half - rod / 2 - 0.3)
        rz = ccz - half + rod / 2 + 0.3
        solid(comp, "Focus spacer (front, print)", box_span(lens_x1 + 0.05, lens_x1 + front_len, ry - rod / 2, ry + rod / 2,
                                                            rz - rod / 2, rz + rod / 2), looks["pin"])
        solid(comp, "Focus spacer (rear, print)", box_span(oled_x1 + 0.05, oled_x1 + rear_len, ry - rod / 2, ry + rod / 2,
                                                           rz - rod / 2, rz + rod / 2), looks["pin"])

    ex, ey, ez = P["eye"]
    solid(comp, "Beam splitter cube 15mm", box(ccx, ccy, ccz, s, s, s), looks["glass"], 0.35)
    along, across = adsk.core.Vector3D.create(1, 1, 0), adsk.core.Vector3D.create(-1, 1, 0)
    along.normalize()
    across.normalize()
    diag = adsk.core.OrientedBoundingBox3D.create(v1.pt(ccx, ccy, ccz), along, across, v1.cm(s * 1.38), v1.cm(0.3), v1.cm(s * 0.98))
    solid(comp, "50/50 coating (diagonal)", tbm.createBox(diag), looks["mirror"])
    solid(comp, "Lens 22mm f27", cyl((lens_x0, ccy, ccz), (lens_x1, ccy, ccz), P["lens_d"] / 2), looks["glass"], 0.4)
    solid(comp, "SSD1306 OLED module", box_span(oled_x0, oled_x1, ccy - pcb / 2, ccy + pcb / 2, ccz - pcb / 2, ccz + pcb / 2),
          looks["pcb"])
    solid(comp, "Light path: OLED to cube", cyl((oled_x0, ccy, ccz), (ccx, ccy, ccz), 0.4), looks["ray"])
    solid(comp, "Light path: cube to eye", cyl((ccx, ccy, ccz), (ex, ey, ez), 0.4), looks["ray"])

    # Tinted window on the tunnel's front face.
    face = ccy + half + w
    t = V2["pod_window_inset"]
    x0, x1, z0, z1 = lens_x1 + 3.0, slide_end - 3.0, ccz + 4.0, ccz + 11.0
    cut_from(frame, FRONT, [box_span(x0, x1, face - t, face + 1, z0, z1)])
    solid(comp, "Tinted pod window (print dark or cut acrylic)", box_span(x0 + c, x1 - c, face - t, face - 0.05, z0 + c, z1 - c),
          looks["tint"], 0.9)


def add_wiring_and_right_slot(frame, looks, cam):
    """Right arm's T-slot, the brow wire groove with its press-fit cover, and the wire passages into the pod, the
    battery arm and the camera housing (all through the one front unit now)."""
    fb, ff = -P["front_t"] / 2, P["front_t"] / 2
    w = P["wall"]
    end_x, end_z = v1.WIRE_PATH[-1]
    wz1 = end_z + P["wire_h"] / 2
    cover_path = [(x, z) for x, z in v1.WIRE_PATH if x <= P["cover_end_x"]]
    rebate_h = P["wire_h"] + 2 * P["cover_overlap"]
    cut_from(frame, FRONT, v1.t_slot(P["rail_right"][0], fb, P["rail_right"][1], P["slide_clear"]) +
             v1.groove_along(v1.WIRE_PATH, fb - 0.1, fb + P["wire_depth"], P["wire_h"]) +
             v1.groove_along(cover_path, fb - 0.1, fb + P["cover_t"], rebate_h) + [
        cyl((end_x, fb + 1.0, end_z), (end_x, ff + w + 0.1, end_z), P["wire_r"]),
        cyl((-end_x, fb + 0.1, end_z), (-end_x, fb - w - 0.1, end_z), P["wire_r"]),
    ])
    strip_parts = v1.groove_along(cover_path, fb, fb + P["cover_t"], rebate_h - P["clear"])
    strip = strip_parts[0]
    for part in strip_parts[1:]:
        tbm.booleanOperation(strip, part, adsk.fusion.BooleanTypes.UnionBooleanType)
    solid(frame, "Wire cover strip (print)", strip, looks["frame"])
    cut_from(frame, "Right temple (battery inside)", [
        box_span(end_x - 3.0, end_x + 1.0, fb - P["pocket_y0"] - 0.5, fb + 0.1, end_z - P["wire_h"] / 2, wz1),
    ])


def looks_for():
    fa = v1.find_appearance
    return {
        "frame": fa("plastic", "matte", "black") or fa("plastic", "black"),
        "tint": fa("glass", "gray") or fa("glass", "smoke") or fa("glass"),
        "glass": fa("glass"),
        "pcb": fa("plastic", "green") or fa("green"),
        "lensblack": fa("rubber", "black") or fa("black"),
        "battery": fa("aluminum") or fa("metal"),
        "mirror": fa("mirror") or fa("chrome"),
        "ray": fa("red"),
        "eye": fa("plastic", "white"),
        "pin": fa("plastic", "white") or fa("white"),
    }


def build(design, looks):
    """Builds the whole v2 assembly into design's root (also used by the assembly-steps script)."""
    root = design.rootComponent
    cam, pod = camera_layout(), pod_layout()
    frame = v1.new_component(root, "Frame (print)")
    rounding = build_front_unit(frame, looks, cam, pod)
    add_lens_seats(frame, looks)
    finish_camera(root, frame, looks, cam)
    finish_pod(root, frame, looks, pod)
    v1.build_temple(frame, looks, -1, cam["y_back"], "Left temple")
    v1.build_temple(frame, looks, 1, -P["front_t"] / 2, "Right temple (battery inside)", battery_pocket=True)
    v1.build_reference(root, looks)
    add_wiring_and_right_slot(frame, looks, cam)
    unit = frame.bRepBodies.itemByName(FRONT)
    unit.name = FRONT_UNIT
    v1.style(unit, looks["frame"], 0.55)
    return rounding


def run(context):
    ui = app.userInterface
    try:
        active = app.activeDocument
        # Match the v2 file exactly: the assembly and soldering step files share the prefix, and this wipes the tab.
        if active and active.dataFile is not None and re.fullmatch(r"Memory Glasses v2( v\d+)?", active.name):
            # Rebuild inside the saved v2 file so saving adds a new version instead of a second file.
            design = adsk.fusion.Design.cast(app.activeProduct)
            design.timeline.moveToBeginning()
            design.timeline.deleteAllAfterMarker()
            for occ in [design.rootComponent.occurrences.item(i) for i in range(design.rootComponent.occurrences.count)]:
                occ.deleteMe()
        else:
            for doc in [app.documents.item(i) for i in range(app.documents.count)]:
                if doc.dataFile is None and doc.name.startswith("Untitled"):
                    doc.close(False)
            app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
            design = adsk.fusion.Design.cast(app.activeProduct)
            design.designType = adsk.fusion.DesignTypes.ParametricDesignType
        if not design.attributes.itemByName("MemoryGlasses", "version"):
            design.attributes.add("MemoryGlasses", "version", "2")
        rounding = build(design, looks_for())

        vp = app.activeViewport
        view = vp.camera
        view.eye = v1.pt(-260, 330, 120)
        view.target = v1.pt(0, -20, 5)
        view.upVector = adsk.core.Vector3D.create(0, 0, 1)
        vp.camera = view
        vp.fit()
        ui.messageBox("MemoryGlassesV2 built (front unit rounding: {}).".format(rounding))
    except Exception:
        ui.messageBox("MemoryGlassesV2 failed:\n{}".format(traceback.format_exc()))
