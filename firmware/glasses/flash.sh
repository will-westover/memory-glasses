#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
BINARY=${1:-glasses}
cargo build --release --bin "$BINARY"

PORT=${PORT:-$(ls /dev/cu.usbmodem* | head -1)}

espflash flash --port "$PORT" --flash-size 8mb --partition-table partitions.csv --erase-parts otadata \
  "target/xtensa-esp32s3-espidf/release/$BINARY"
