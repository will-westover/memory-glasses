"""Restart the board, read the photo it prints over USB and save it as a JPEG.

    uv run --with pyserial python read_photo.py /dev/cu.usbmodem101 photo.jpg
"""

import base64
import sys
import time

import serial


def restart(port_name: str) -> None:
    port = serial.Serial(port_name, 115200, timeout=1)
    port.dtr = False
    port.rts = True
    time.sleep(0.2)
    port.rts = False
    port.close()
    time.sleep(1.5)


def reopen(port_name: str) -> serial.Serial:
    for _ in range(20):
        try:
            return serial.Serial(port_name, 115200, timeout=1)
        except serial.SerialException:
            time.sleep(0.5)

    raise SystemExit(f"could not open {port_name}")


def main() -> int:
    port_name, out = sys.argv[1], sys.argv[2]
    restart(port_name)
    port = reopen(port_name)

    inside, data = False, []
    deadline = time.time() + 40

    while time.time() < deadline:
        line = port.readline().decode(errors="replace").strip()

        if line.startswith("PHOTO"):
            inside = True
            print(line)
        elif line == "END PHOTO":
            with open(out, "wb") as photo:
                photo.write(base64.b64decode("".join(data)))
            print(f"saved {out}")
            return 0
        elif inside:
            data.append(line)
        elif line:
            print(line)

    print("no photo before the timeout")
    return 1


if __name__ == "__main__":
    sys.exit(main())
