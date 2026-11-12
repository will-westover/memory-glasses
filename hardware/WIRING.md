# Memory Glasses v2: soldering guide

Pins and battery polarity are from Seeed's [XIAO ESP32-S3 pinout](https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/).
Wire lengths come from the v2 CAD routing and include slack for taking the arms off.

To see every wire and joint in 3D, open **Memory Glasses v2 - Soldering steps** in Fusion, which has 11 numbered steps. Pictures
of each step are in [cad/renders/soldering-steps](cad/renders/soldering-steps), with `-closeup` versions for the XIAO pads.
XIAO pad positions there are measured from Seeed's model; the display, switch and battery are simplified stand-ins and the
wires are drawn short.

## Connections

| From (XIAO back) | Wire | Length | To |
|---|---|---|---|
| `3V3` | red | 230 mm | OLED `VCC` (or `VDD`) |
| `GND` | black | 230 mm | OLED `GND` |
| `D4` | yellow | 230 mm | OLED `SDA` |
| `D5` | green | 230 mm | OLED `SCL` |
| `BAT+` pad | white | 260 mm | Switch **middle** pin |
| `BAT-` pad | blue | 260 mm | Battery **black** lead |
| Switch **outer** pin (either one) | battery lead | | Battery **red** lead |

Colors are only a suggestion. Don't use red or black for the battery wires, so they can't be mixed up with the display's.

## Where the pads are

Hold the XIAO with its **back** facing you (labels printed) and **USB at the top**:

```
              USB
   VUSB  o           o  D0
    GND  o           o  D1
    3V3  o     [-]   o  D2
    D10  o     [+]   o  D3      [-] BAT-  closer to USB
     D9  o           o  D4      [+] BAT+  farther from USB
     D8  o           o  D5
     D7  o           o  D6
```

Check the labels printed on the board against this before each joint. On the OLED, go by the labels printed on
the board, because pin order varies between modules.

## Order

Keep USB unplugged and the battery out until step 7.

1. **Set up:** iron around 350 C, ventilation, damp sponge, and tape or helping hands to hold the board. Practice one joint
   on scrap wire.
2. **Prep each wire:** strip 2 mm, twist, and coat with solder (tin it). Tin the pad too.
3. **XIAO display wires:** `3V3`, `GND`, `D4`, `D5`. Wires go straight onto the pads, no pin headers (the housing has
   no room for them). Heat, touch wire to pad, let the solder flow, about 2 seconds, then hold still until it sets. Keep solder
   off the metal shield and away from neighboring pads.
4. **XIAO battery wires:** white to `BAT+`, blue to `BAT-`. These pads are small and close together, so check for a bridge.
5. **OLED:** match each wire by the printed labels. If the module has pins already, clip them to about 1 mm and solder to those.
6. **Switch:** white to the middle pin, plus a short wire from one outer pin for the battery's red lead.
7. **Battery, last:** if it has a connector, cut the leads **one at a time** so they can't touch. Tin and solder the red lead to
   the switch wire, then the black lead to the blue wire. Keep the iron away from the cell. Wrap each joint in
   electrical tape or heat shrink.
8. **Check before power:** look for solder bridges with a magnifier or phone camera. With a multimeter, confirm no short between
   `3V3` and `GND`, or between `BAT+` and `BAT-`.

## Test it

Upload `firmware/display_test/display_test.ino` (setup is in the sketch's header comment) before running any wires
through printed parts. The display should show full white, then a border, then a counter.

## What to expect

- **Charging:** the switch cuts the battery off completely, so it must be ON to charge over USB.
- **Battery percentage:** the XIAO has no pin that reads battery voltage, so software can't show it without extra wiring.
- **Battery warning:** stop and disconnect if the battery gets hot or swells. Never let bare leads touch.

## Checked for the final print (2026-11-06)

1. **Wire groove.** The brow groove is now 1.6 x 2.2 mm under the cover strip and the holes are 2.4 mm, which fits six
   wires up to about 0.7 mm across (30 AWG silicone is about 0.6 mm).
2. **Battery.** It slid into the fit-test pocket slice, so the pocket is unchanged. The space beside the cell is still
   tight for the wires that run past it to the switch. Press them flat against the lid side, and trim any slack there.

## Threading through the printed parts

The holes are too small for the boards, so wires have to pass through before the far ends are connected. Once the front unit
is printed, desolder the OLED and switch ends, thread the wires from the camera housing through the brow, and solder them again.
