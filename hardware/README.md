# Memory Glasses hardware

Everything physical: the CAD model, print files, parts list, wiring and assembly. The software is described in
the [project README](../README.md). Paths below are relative to this `hardware/` folder.

## Layout

```
cad/
  fusion/MemoryGlasses/        Fusion script that builds the whole assembly; every size lives in P at the top
  fusion/MemoryGlassesV2/      v2 styling option: same parts and internals, flat brow, modules printed with the front
  fusion/MemoryGlassesExport/  Exports v2's printed parts as raw STLs
  fusion/MemoryGlassesSteps/   Builds the "v2 - Assembly steps" file: 15 exploded steps with arrows and labels
  renders/assembly-steps/      A picture of each assembly step (step-01..15.png) plus overview.png
  fusion/MemoryGlassesSolder/  Builds the "v2 - Soldering steps" file: 11 steps showing every wire and joint in WIRING.md
  renders/soldering-steps/     A picture of each soldering step, plus close-ups of the XIAO pads
  fusion/MemoryGlassesCheck/   Fusion script: lists every body and reports any overlapping parts
  fusion/MemoryGlassesShowroom/  Fusion script: real-life materials + photoreal renders into renders/
  print/fit-test/              STLs to print first (written by MemoryGlasses)
  print/v2/                    Ready-to-print v2 parts, already oriented, with a print guide (README.md there)
  print/prepare_v2_stls.py     Orients and checks the raw STLs from fusion/MemoryGlassesExport
  reports/check-report.txt     Latest collision-check output
  renders/                     Photoreal renders of v2
  vendor/                      Manufacturer models and drawings; see "Vendor models" below
```

All scripts are linked into Fusion's Scripts folder. In Fusion: Shift+S, search "MemoryGlasses", Run.
Each build closes earlier unsaved "Untitled" documents, so save anything of your own before running it.
v1 is saved in Fusion as "Memory Glasses" and v2 as "Memory Glasses v2". Running V2 while the saved v2 file is
the open tab rebuilds inside that file, so Cmd+S saves a new version of it. V2 reuses v1's code for everything it doesn't restyle, and tags
its document so the check and render scripts write `check-report-v2.txt` and `photoreal-v2-*.png` instead
of overwriting v1's.

## Parts (order these)

| Part | Link | Price |
|---|---|---|
| XIAO ESP32-S3 Sense (x1) | [Amazon](https://www.amazon.com/dp/B0C69FFVHH) or Mouser/Digi-Key part 113991115 (faster) | $23.99 |
| 0.96" SSD1306 OLED, white | [B06XRBTBTB](https://www.amazon.com/dp/B06XRBTBTB) | $6.99 |
| 15mm 50/50 beam-splitter cube | [B08FBCKKRM](https://www.amazon.com/dp/B08FBCKKRM) | $14.99 |
| 22mm f27 glass lens (2-pack) | [B07T2LYQK1](https://www.amazon.com/dp/B07T2LYQK1) | $5.65 |
| 601452 LiPo 500mAh | [B09GFS1VFR](https://www.amazon.com/dp/B09GFS1VFR) | $9.99 |
| ThunderClap clear lenses, Ray-Ban Meta Wayfarer **50mm** | [B0FRF65VC2](https://www.amazon.com/dp/B0FRF65VC2) | $18.99 |
| SS12D00 slide switches | [B0GXB39TVL](https://www.amazon.com/dp/B0GXB39TVL) | $6.99 |
| 30 AWG silicone wire | [B01M70EDCW](https://www.amazon.com/dp/B01M70EDCW) | $10.48 |
| Superglue gel | any | ~$6 |
| Soldering kit (iron, solder, helping hands) | [B0GCMVP12Q](https://www.amazon.com/dp/B0GCMVP12Q) | $35.99 |

Dowel pins are cut from 1.75mm filament. The microSD card is optional (frames stream over Wi-Fi; the card is only
an offline buffer) and the camera block still leaves its slot reachable.

Soldering steps and pin connections: see [WIRING.md](WIRING.md).

## How it goes together

v1 glues the camera block and the optics pod's base to the front on 1.75mm filament dowels. **v2 prints them as
one piece with the front** (the "front unit": the shells are joined first and rounded in one pass, so the
modules blend into the frame), so v2 needs no glue or dowels. Print the v2 front unit standing as worn (about 188 x 62 x 52mm):
both modules open at the top, so their insides stay clear, but it needs supports and a brim (see
`cad/print/v2/README.md`). Everything else slides, drops in or
press-fits, so it can come apart again:

| Part | How it attaches |
|---|---|
| Left arm | T-rail on the arm slides down into a T-slot in the camera block's back wall |
| Right arm | T-rail slides down into a T-slot in the back of the frame's right corner |
| Battery + switch lid | Pencil-box lid slides out through the right arm's front face; the frame holds it shut once the arm is on |
| XIAO board | Drops into the camera block from the top; the walls line the camera up with its window |
| Camera block cap | Pencil-box cap slides in from the outer side |
| Cube + 22mm lens | Sit in the pod base; the top half lifts off on two unglued friction dowels |
| Display | Sits in a printed sled that slides in the pod tunnel; front/rear spacer rods set the focus |
| Pod tunnel lid | Pencil-box lid slides in from the far end |
| Frame lenses | Drop in from the back; three press-fit tabs per lens hold them |
| Brow wires | Run in the groove behind the brow under a press-fit cover strip |

## Build order (keeps printing to a minimum)

1. Print the fit-test pieces in `cad/print/fit-test/` (one small plate, PETG or PLA): dowel holes, slide-fit
   T-slots + rail, lens seat corner, battery pocket slice, switch pocket, USB-C/SD opening, and the two
   focus-spacer combs (keep these; they're the real spacers).
2. Slide the test rail through each T-slot (0.15 / 0.25 / 0.35mm gaps) and report the smoothest one; it sets
   every sliding fit. When parts arrive, also check: dowel snugness, lens in the seat corner, battery through the
   pocket slice, switch in its pocket, a USB-C cable into the opening. Caliper the battery.
3. Report what was tight or loose; the numbers in `P` get adjusted and the script rebuilds everything.
4. Bench-test the electronics and HUD optics on a desk before building them into anything.
5. v1: print the front alone; check lens fit and face fit. v2: the front unit includes the camera housing and
   pod, so check the fit-test results especially carefully first; a mistake there reprints the whole front.
6. Print the arms, then (v1) the camera block and optics pod, or (v2) the caps, lids and display sled. PETG for
   anything around the XIAO: it runs warm.

## Still unverified (marked MEASURE in `P`)

Battery size (no datasheet; listings say 4mm or 6mm thick), lens curve and edge thickness, U.FL antenna size,
eye position, and fit on your face. Everything else is from a datasheet, a listing, or a manufacturer model, and
the assembly has no overlapping parts.

## Vendor models

Files are in `cad/vendor/`.

| Part | File | Source | Status |
|---|---|---|---|
| XIAO ESP32-S3 Sense | `xiao-esp32s3-sense/model/Seeed Studio XIAO-ESP32-S3-Sense.step` | Seeed official, [wiki resources](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/) | Imported into the assembly and collision-checked |
| XIAO ESP32-S3 Sense | `xiao-esp32s3-sense/*.dxf` | Seeed official 2D dimension drawings | Reference |
| XIAO ESP32-S3 Sense | `xiao-esp32s3-sense/seeed-housing-*.stp` | Seeed's own reference enclosure | Reference |
| 0.96" SSD1306 OLED | `oled-ssd1306-096/markoovv23_oled_096_ssd1306.step` | Community model, [markoovv23/lora](https://github.com/markoovv23/lora) | No official CAD exists for generic modules; agrees with the HiLetgo listing (27.0 x 27.0 x 4.1, active 21.74 x 11.2) within 0.3mm |
| SS12D00 slide switch | `switch-ss12d00/CK_SS_series.pdf` | C&K SS series catalog (mirror: static.chipdip.ru) | Original-maker drawing of the family the SS12D00 clones: body 8.6 x 3.5 x 3.5, knob 1.5, travel 2 |
| ThunderClap lenses (RW4006 50-22) | none | Published frame size: lens 50 x 41, bridge 22, hinge-to-hinge 131, temple 150 ([VR Optician](https://vroptician.com/prescription-lens-inserts/ray-ban-meta-wayfarer-50)) | Lens outline traced from a photo, scaled to 50 x 41 |

No published design exists for: the 601452 LiPo (listings disagree, 4mm vs 6mm thick), the 15mm beam-splitter cube
and 22mm lens (listing dimensions only; both are simple shapes).
Check community-model licenses before redistributing them.
