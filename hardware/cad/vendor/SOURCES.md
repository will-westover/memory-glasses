# Vendor models and drawings

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
