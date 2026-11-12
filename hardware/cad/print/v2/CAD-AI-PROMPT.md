Build a parametric CAD model of 3D-printed "memory glasses": a Ray-Ban Meta Wayfarer-style frame with a camera module on the left and a heads-up display pod on the right. Every dimension must live in one parameter table at the top so fits can be tuned and the whole model regenerated. Don't scale parts to fix fits; change the parameters.

## Coordinates and units

Use millimetres. +X is the wearer's right, +Y is forward (away from the face), +Z is up. The origin is the centre of the front frame.

## Off-the-shelf parts the model must hold

| Part | Envelope (mm) | Notes |
|---|---|---|
| Seeed XIAO ESP32-S3 Sense | 24.42 long x 17.80 wide x 14.00 deep | Mounted standing up, with USB-C and microSD at the bottom. The camera lens axis sits 5.54 mm from the envelope centre toward the USB end and 0.64 mm across. Use Seeed's STEP model if you can. |
| 0.96" SSD1306 OLED (HiLetgo) | PCB 27.0 x 27.0 x 4.1 | The 21.74 x 11.2 active area is off-centre on the PCB. |
| 50/50 beam-splitter cube | 15 x 15 x 15 | |
| Plano-convex glass lens | 22 dia x 7 thick, f = 27 | |
| 601452 LiPo battery | 6 x 14 x 52 | Leave room to measure the real cell. |
| SS12D00 slide switch | Pocket 9.2 x 4.0 x 6.5 | |
| Frame lenses (ThunderClap for Ray-Ban Meta Wayfarer 50 mm) | 50 x 41 | Base-6 curve (about 88.3 mm radius), 2 mm centre thickness |
| Six 30 AWG silicone wires | about 0.6 mm dia each | |

## Front frame

- **Front outline:** 7.0 mm thick, extruded symmetric about Y = 0, and about 150.6 mm wide.
  - The right half, mirrored for the left, runs as (x, z) from top centre clockwise: (0,23.0) (7.3,23.6) (14.5,25.2) (21.7,26.6) (29.0,27.4) (36.2,27.8) (43.4,28.1) (50.6,28.1) (57.9,28.1) (65.1,28.1) (72.3,28.1) (74.6,27.4) (75.3,24.0) (75.2,19.5) (74.8,14.7) (74.6,12.3) (73.0,9.9) (69.9,7.5) (67.8,2.6) (66.5,-2.2) (65.3,-7.0) (63.8,-11.8) (61.5,-16.6) (58.6,-19.8) (52.6,-23.8) (46.5,-24.1) (34.5,-24.2) (28.5,-23.8) (24.5,-22.2) (20.9,-20.2) (17.5,-17.8) (13.4,-13.0) (10.5,-8.2) (8.4,-3.4) (6.4,1.4) (4.3,6.3) (0,7.0).
  - Run a closed spline through the points.
  - The brow is flat at Z = 28.1, which is also the top of both modules.
- **Lens openings:** a Wayfarer lens shape 50 x 41 for each eye, centred about X = ±37.4.
  - The seat has a 1.2 mm lip and is inset 1.0 mm, with 0.2 mm clearance around the lens.
  - The lenses drop in from the back. Three press-fit retainer tabs per lens hold them; each tab is 6 x 2.5 x 1 mm, six in total.
- **Wire groove** behind the brow, running the full width of the frame:
  - 1.6 tall x 3.0 deep, following the centreline (x, z): (0,13.0) (8,14.7) (14,17.9) (21,19.9) (28.5,21.2) (40.5,22.3) (52.6,22.0) (60,21.3) (66,20.3) (73,19.0), mirrored.
  - Closed by a press-fit cover strip 0.8 mm thick that overlaps the groove 0.4 mm on each side and ends at X = ±66.
  - Pass-through holes are 2.4 mm diameter.
  - The groove carries six wires: four from the camera module to the display, and two to the right arm's battery and switch.

## Camera module (left, printed as one piece with the front)

- **Housing:** a box behind the front's left end.
  - X runs from -90.4 to -67.6, Y from -24.5 to -3.5, and Z from about -2.5 to 28.1.
  - Walls are 1.6 mm. The back wall is 5.0 mm, because it holds the left arm's T-slot.
- **XIAO fit:** the XIAO drops in from the top with 0.2 mm clearance.
  - Its centre is at Z = 11.5, with its camera facing +Y.
  - Leave a 1.4 mm gap on the outer side for the flat U.FL antenna.
- **Camera window:** 9 mm, through the front wall on the camera axis, with a recessed ring 5.5 mm wide and 0.6 mm deep around it.
- **USB-C and microSD opening:** at the bottom, 11.8 wide x 8.5 deep from the back of the board. A USB-C plug head (13 x 7) must fit.
- **Top cap:** a 2 mm sliding "pencil-box" cap with a 0.6 mm tongue closes the top and slides in from the outer side.

## Display pod (right, printed as one piece with the front)

- **Eye position:** (37.4, -18, 0). Leave this as a parameter, because it depends on the wearer's face.
- **Cube:** centred at X = 51.4, Y = 20.6, Z = 11.0, in front of the right lens, inside a cage with 1.6 mm walls. Its 45° face turns the display image back toward the right eye, so the HUD appears up and to the right.
- **Tunnel:** runs from the cube in +X past the right temple, with a 31 x 31 inner section (OLED PCB plus a 2 mm shim each side) and a top at Z = 28.1.
- **Stations along X:**
  - Lens from X = 59.4 to 66.4, centred at 62.9.
  - Display sled travel from the lens centre plus 0.70f to plus 1.05f, which is about X = 81.8 to 91.3 for the OLED face. The nominal position is 0.90f, about X = 87.2.
  - The tunnel ends at about X = 95.4 plus the wall.
- **Display sled:** 29 x 29 x 5. It slides in the tunnel and holds the OLED behind a 1.2 mm lip with a 23 x 13 window.
- **Focus spacers:** 3 x 3 mm rods fix the sled's position. Make two front spacers 19.5 long and two rear spacers 3.9 long, plus a comb of other lengths for focusing.
- **Cube-and-lens cap:** a removable top half over the cube and lens, located by two friction dowels cut from 1.75 mm filament (1.9 mm holes, 4.2 deep).
- **Tunnel lid:** a sliding pencil-box lid that goes in from the far end.
- **Window:** optional tinted window, 23 x 7 x 0.6, on the pod's front face.

## Arms (temples)

- **Side profile (y, z)**, starting at the arm's front end: level with the brow (Z 23.4 to 5.0) for the first stretch, then tapering, then a gentle ear drop. The ear end is at about Y = -153.
  - The left arm is about 131 mm long, because it starts behind the camera housing.
  - The right arm is about 152 mm long.
- **Right arm:** 9.6 mm thick, with a 2.5 mm fillet.
  - It holds the battery pocket (6.4 slot) and the switch pocket.
  - A 1.6 mm sliding lid (0.8 lip plus 0.8 tongue) slides out through the arm's front face. The frame blocks the lid once the arm is attached.
  - The arm is 18.4 mm tall through the pockets so the lid grooves keep 1.2 mm walls.
- **Attachment:** each arm has a vertical T-rail on its front end that slides down into a T-slot.
  - The rail stem is 2.0 wide x 1.5 deep, and the flange is 4.0 wide x 1.2 deep.
  - The left slot is in the camera housing's back wall at X = -71.0, spanning Z 8 to 20.
  - The right slot is in the back of the frame's right corner at X = 70.6, spanning Z 11.5 to 17.5. It stops below where the wires pass into the arm.

## Fits (tested on a real print)

- **Sliding fits** (T-rails, lids, caps): 0.15 mm gap per side. Make this a single parameter.
- **General clearance** around purchased parts: 0.2 mm.
- **Tongues and grooves:** the tongue is 0.8, the lip 0.8, and grooves sit 0.75 under the top face and 0.75 inside the wall.

## Printed parts to output

1. **Front unit.** Frame, camera housing and pod housing as one solid, about 187 x 62 x 52 mm.
   - Join the three outer shells first, then fillet them in one pass: 2.5 mm on the module edges and blends, 1.5 mm on the top edges.
   - Cut the cavities, lens seats, slots and grooves after the fillet, so thin walls never get filleted.
2. Left arm.
3. Right arm.
4. Battery lid.
5. Camera cap.
6. Cube-and-lens cap.
7. Tunnel lid.
8. Display sled.
9. Focus spacers: two front, two rear.
10. Wire cover strip.
11. Lens retainer tabs, six.
12. Optional tinted window.

## Print constraints

- **Process:** FDM with a 0.4 mm nozzle, in PETG (the XIAO runs warm) or PLA.
  - Minimum feature size is about 0.6 mm, and walls should be at least 1.2 mm.
- **Front unit orientation:** printed standing as worn, with both modules open at the top so their insides print clean.
  - It needs tree supports and a 10 mm brim.
  - Avoid support inside the T-slots, lid grooves and cavities.
- **Other parts:** orient each one flat on its largest face with no supports, except under the cube cap's raised section.
- **Plate:** all parts must fit on one 220 x 220 mm bed with at least 5 mm between parts.

## Deliverables

- The parametric model, with every size above in one table.
- Each printed part exported as an STL already in print orientation, plus one combined 220 x 220 plate STL.
- A STEP of the full assembly with the purchased parts placed in it.
- An interference check showing no part overlaps another and that every purchased part fits its pocket with the stated clearance.
- A small fit-test print:
  - A block with T-slots at 0.15, 0.25 and 0.35 mm gaps plus one rail.
  - Dowel holes from 1.80 to 2.00 mm.
  - A lens-seat corner.
  - A battery pocket slice.
  - A switch pocket.
  - The USB-C opening.
