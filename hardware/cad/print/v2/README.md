# Memory Glasses v2: print files

**Just printing?** Use `ready-to-print/memory-glasses-plate.stl`: every part on one 220 x 220 mm bed, with a
five-step note beside it. Rebuild it with `uv run --with numpy python hardware/cad/print/make_v2_plate.py`.

Every STL is already rotated to its print orientation, centred and sitting on the bed. Import it and don't rotate
it. The `x2` or `x6` at the end of a file name is how many to print. `mesh-check.txt` has the check results.

**These are the final parts.** They use the fit-test results from BYU's print (2026-11-06): 0.15 mm sliding gaps,
the tightest slot the test rail slid through. The battery, switch and USB-C pockets fit as they were. Ask BYU to use
the same printer and material as the fit test, because the gaps only hold on that setup.

## Settings for everything

- PETG or ASA rather than PLA (the XIAO runs warm), 0.4 mm nozzle, 0.16 mm layers if offered, 4 walls, 30% infill
- Total: about 73 cm³, roughly 95 g of PETG if solid, plus supports
- Bed: the front unit needs about 190 mm of width before its brim, so any BYU FDM printer works

## Parts

| File | Qty | Size (mm) | Lies on the bed | Supports |
|---|---|---|---|---|
| `01-front-unit` | 1 | 187 x 62 x 52 | Standing as worn | **Yes, see below** |
| `02-left-arm` | 1 | 28 x 131 x 10 | Outer face | Off |
| `03-right-arm` | 1 | 28 x 152 x 10 | Outer face, battery pocket up | Off |
| `04-battery-lid` | 1 | 16 x 68 x 1.6 | Tongue side | Off |
| `05-camera-cap` | 1 | 21 x 16 x 2 | Tongue side | Off |
| `06-cube-lens-cap` | 1 | 24 x 34 x 17 | Top face, cavities up | Under the raised cube section only |
| `07-tunnel-lid` | 1 | 30 x 32 x 2 | Tongue side | Off |
| `08-display-sled` | 1 | 29 x 29 x 5 | Window lip | Off |
| `09-focus-spacer-front` | 2 | 19.5 x 3 x 3 | Long side | Off |
| `10-focus-spacer-rear` | 2 | 3.9 x 3 x 3 | Long side | Off |
| `11-wire-cover-strip` | 1 | 134 x 12 x 0.8 | Flat | Off |
| `12-lens-retainer-tab` | 6 | 6 x 2.5 x 1 | Flat | Off; print a few spares |
| `13-tinted-window-optional` | 1 | 23 x 7 x 0.6 | Flat | Off; dark filament, or cut tinted acrylic instead |

**Not printed:** cut two friction dowels, 6 mm long, from 1.75 mm filament. The `focus spacers` combs in `../fit-test/`
hold the other spacer lengths for focusing the display.

## The front unit is the hard one

It's a big, awkward print, so save it for last, once the fit test and smaller parts have come out right.

- **It barely touches the bed** (about 41 mm², just the bottoms of the two rims). Use a 10 mm brim, a clean bed and
  a slow first layer.
- **Use tree supports set to "everywhere," not "from build plate only."** About 1,070 mm² of the overhang sits above
  the part itself, mainly the tops of the lens openings above the lower rims. Build-plate-only leaves those unsupported.
- **Supports go under:** the pod and camera housing, the tops of both lens openings, and the outer curve of the
  lower rims.
- **Keep supports out of the slots** if your slicer allows it (support blockers): both arm T-slots, the cap and lid
  grooves, and the camera and pod cavities. Anything left in a groove stops a lid from sliding.
- **Clean up afterward:** check that a lens drops into each seat and the cap and lid slide before you continue. Scrape
  support marks out of the lens seats gently, because the lenses rely on those surfaces.

## Other things to watch

- **Right arm:** the battery lid's grooves are small 1 mm overhangs along the pocket. Leave supports off, because
  support inside them is very hard to remove. Clear any droop with a blade.
- **Thin parts:** the lid tongues (0.6 mm), tabs (1 mm), strip (0.8 mm) and window (0.6 mm) are close to the nozzle
  size. If they print rough, slow down rather than scaling them.
- **Don't scale any file.** Fix fit problems by changing the numbers in the CAD and regenerating.

## Printing at BYU (Harold B. Lee Library, The Workshop)

Staff do the printing; you hand over files and pay. Send them `ready-to-print/memory-glasses-plate.stl`. Details checked
2026-10-30 from [the service page](https://lib.byu.edu/services/3d-printers/) and
[the 3D printing guide](https://guides.lib.byu.edu/3Dprinting).

- **Where:** Workshop Help Desk, library Level 4, Room 4840. workshop@byu.edu, 801-422-8981.
- **Desk hours:** Mon-Thu 8am-9pm, Fri 8am-6pm, Sat 10am-6pm. Closed Tue and Thu 10:45-12:00.
- **How:** bring the files on a flash drive or email them. If you only email, include dimensions and specs.
- **Payment:** BYU Signature card only (CougarCash or department card).
- **Cost:** $0.10 per gram for PLA or PETG, weighed after printing and including supports. Ask for the estimate first.
- **Timing:** they queue it, estimate price and time, and email when it's done. No guaranteed dates, but academic
  prints get priority. Pick up within two weeks or the cost becomes a library fine.
- **Failed prints:** you pay if the model caused it, not if staff or the printer did.
- **Proof:** staff may ask you to show it's your own creation or for a class, sometimes with written confirmation
  from your instructor. Have that ready.

**Their pages disagree, so confirm at the desk:**

- **Materials:** the service page says PLA, PETG, TPU, ASA, resin; the guide says PLA (black or white), PETG (white
  only), TPU, nylon; the policy page says PLA only. It's worth asking whether black ASA or PETG exists.
- **Layer height:** the guide lists 0.2 mm, not the 0.16 mm suggested above. Ask whether 0.16 is possible; 0.2 will
  still work, with rougher thin parts.
- **Resin:** the listed printer and prices differ between pages. Stick with FDM.

## Regenerating after a change

1. In Fusion, open the "Memory Glasses v2" tab and run `MemoryGlassesV2` (after editing `P` or `V2` if a number
   changed), then `MemoryGlassesCheck`.
2. Run `MemoryGlassesExport` with that tab still open.
3. Run `python3 hardware/cad/print/prepare_v2_stls.py`. It rewrites these files and `mesh-check.txt`. The raw exports in
   `_raw/` can be deleted afterward.
