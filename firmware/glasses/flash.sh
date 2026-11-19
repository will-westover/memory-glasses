#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
cargo build --release --bin "${1:-glasses}"

BOOTLOADER=$(ls target/xtensa-esp32s3-espidf/release/build/esp-idf-sys-*/out/build/bootloader/bootloader.bin | head -1)
PORT=${PORT:-$(ls /dev/cu.usbmodem* | head -1)}

espflash flash --port "$PORT" --flash-size 8mb --bootloader "$BOOTLOADER" --partition-table partitions.csv --erase-parts otadata \
  "target/xtensa-esp32s3-espidf/release/${1:-glasses}"
