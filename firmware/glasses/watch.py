"""Print what the board says over USB, reconnecting when it restarts or is unplugged."""

import glob
import time

import serial

while True:
    ports = glob.glob("/dev/cu.usbmodem*")

    if not ports:
        print("waiting for the board on USB...")
        time.sleep(1)
        continue

    try:
        with serial.Serial(ports[0], 115200, timeout=1) as port:
            while True:
                line = port.readline().decode(errors="replace").rstrip()

                if line and not line.startswith(("I (", "W (")):
                    print(line, flush=True)
    except (serial.SerialException, OSError):
        print("board disconnected")
        time.sleep(1)
