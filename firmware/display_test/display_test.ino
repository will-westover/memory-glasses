// Bench test for the soldered Memory Glasses electronics: XIAO ESP32-S3 Sense + 0.96" SSD1306 OLED.
// Serial Monitor (115200) reports what it finds. Unplug USB with the switch on to test the battery:
// if the seconds counter keeps going on the display, the battery and switch wiring work.
//
// Libraries: Adafruit SSD1306 and Adafruit GFX. Board: XIAO_ESP32S3. Set Tools > USB CDC On Boot > Enabled.

#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Wire.h>

const int SCREEN_W = 128;
const int SCREEN_H = 64;
// The XIAO's LED is on when its pin is LOW.
const int LED_ON = LOW;
const int LED_OFF = HIGH;

Adafruit_SSD1306 display(SCREEN_W, SCREEN_H, &Wire, -1);

// Returns the first display address that answers, or 0. These modules use 0x3C, occasionally 0x3D.
uint8_t scanI2C() {
  uint8_t found = 0;
  int count = 0;
  Serial.println("Scanning I2C on D4 (SDA) and D5 (SCL)...");
  for (uint8_t addr = 1; addr < 127; addr++) {
    Wire.beginTransmission(addr);
    if (Wire.endTransmission() == 0) {
      Serial.printf("  Device at 0x%02X\n", addr);
      count++;
      if (addr == 0x3C || addr == 0x3D) {
        found = addr;
      }
    }
  }
  if (count == 0) {
    Serial.println("  Nothing answered.");
  }
  return found;
}

// Fast blink forever: the board runs, but the display failed.
void failForever(const char *why) {
  Serial.println(why);
  Serial.println("FAIL: check VCC, GND, SDA and SCL against hardware/WIRING.md, and look for solder bridges.");
  while (true) {
    digitalWrite(LED_BUILTIN, LED_ON);
    delay(100);
    digitalWrite(LED_BUILTIN, LED_OFF);
    delay(100);
  }
}

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LED_ON);

  Serial.begin(115200);
  // Wait briefly for the Serial Monitor, but keep going on battery where it never connects.
  unsigned long start = millis();
  while (!Serial && millis() - start < 3000) {
    delay(10);
  }
  Serial.println("\nMemory Glasses display test");

  Wire.begin(D4, D5);
  uint8_t addr = scanI2C();
  if (addr == 0) {
    failForever("No display found at 0x3C or 0x3D.");
  }
  if (!display.begin(SSD1306_SWITCHCAPVCC, addr)) {
    failForever("Display found, but it wouldn't start (not enough memory).");
  }
  Serial.printf("OK: display found at 0x%02X\n", addr);

  // Full white shows any dead pixels or a bad connection.
  display.clearDisplay();
  display.fillRect(0, 0, SCREEN_W, SCREEN_H, SSD1306_WHITE);
  display.display();
  delay(1500);

  // A border shows whether the whole panel is drawn and where its edges are.
  display.clearDisplay();
  display.drawRect(0, 0, SCREEN_W, SCREEN_H, SSD1306_WHITE);
  display.display();
  delay(1500);
}

void loop() {
  unsigned long seconds = millis() / 1000;

  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);
  display.setTextSize(1);
  display.setCursor(0, 0);
  display.println("MEMORY GLASSES");
  display.println("Display OK");
  display.setTextSize(3);
  display.setCursor(0, 32);
  display.print(seconds);
  display.display();

  Serial.printf("Running: %lu s\n", seconds);

  // Slow blink: everything is working.
  digitalWrite(LED_BUILTIN, seconds % 2 == 0 ? LED_ON : LED_OFF);
  delay(1000);
}
