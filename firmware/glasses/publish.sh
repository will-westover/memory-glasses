#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
cargo build --release --bin glasses

OUT=$(mktemp -d)
IMAGE="$OUT/glasses.bin"
espflash save-image --chip esp32s3 --flash-size 8mb --partition-table partitions.csv target/xtensa-esp32s3-espidf/release/glasses "$IMAGE"

VERSION=$(strings target/xtensa-esp32s3-espidf/release/glasses | grep -m1 -E '^[0-9]{8}-[0-9]{4}-[0-9a-f]+$' || date -u +%Y%m%d-%H%M)
SIZE=$(stat -f %z "$IMAGE")
CRC=$(python3 -c "import sys, zlib; print(zlib.crc32(open(sys.argv[1], 'rb').read()))" "$IMAGE")
KEY="firmware/glasses-$VERSION.bin"

printf '{"version": "%s", "size": %s, "crc32": %s, "key": "%s"}\n' "$VERSION" "$SIZE" "$CRC" "$KEY" > "$OUT/latest.json"

cd ../../server
uv run python -m memory.storage put "$IMAGE" "$KEY"
uv run python -m memory.storage put "$OUT/latest.json" firmware/latest.json
cat "$OUT/latest.json"
