# Decisions

Newest first. Each entry says what was chosen, what was rejected, and why.

## Near-duplicate frames are dropped by fingerprint (2026-11-11)

Each frame gets a 64-bit fingerprint: shrink it to 9 x 8 grey pixels and record whether each pixel is brighter
than the one to its right. A frame is dropped when its fingerprint differs from the last kept frame's in 6 bits
or fewer. Comparing against the last kept frame, not the previous frame, means a slow change still gets a new
frame once it adds up.

Rejected: comparing embeddings (costs a model call per frame, which is what this step exists to avoid).

## Firmware in Rust (2026-11-11)

The user chose Rust over C++ for the glasses. It runs on ESP-IDF through `esp-idf-svc`, so Wi-Fi, Bluetooth and
the camera use Espressif's C drivers underneath.

Rejected: Rust without ESP-IDF (`esp-hal`). It can receive camera data, but nothing in Rust sets up the OV2640
sensor, so that would have to be written by hand.

## Server in Python, not Rust (2026-11-11)

Nearly all server time is spent waiting on model calls, and the heavy local work (decoding video, resizing
frames) already runs in `ffmpeg`.
