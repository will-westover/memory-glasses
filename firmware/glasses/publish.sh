#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
cargo build --release --bin glasses

WORK_DIR=$(mktemp -d)
IMAGE="$WORK_DIR/glasses.bin"
espflash save-image --chip esp32s3 --flash-size 8mb --partition-table partitions.csv target/xtensa-esp32s3-espidf/release/glasses "$IMAGE"

VERSION=$(strings target/xtensa-esp32s3-espidf/release/glasses | grep -E '^[0-9]{8}-[0-9]{4}-[0-9a-f]+$' | head -1 || true)
VERSION=${VERSION:-$(date -u +%Y%m%d-%H%M)}
SIZE=$(stat -f %z "$IMAGE")
CRC=$(python3 -c "import sys, zlib; print(zlib.crc32(open(sys.argv[1], 'rb').read()))" "$IMAGE")
STORAGE_KEY="firmware/glasses-$VERSION.bin"

printf '{"version": "%s", "size": %s, "crc32": %s, "key": "%s"}\n' "$VERSION" "$SIZE" "$CRC" "$STORAGE_KEY" > "$WORK_DIR/latest.json"

cd ../../server
uv run python -m server.shared.storage put "$IMAGE" "$STORAGE_KEY"
uv run python -m server.shared.storage put "$WORK_DIR/latest.json" firmware/latest.json
cat "$WORK_DIR/latest.json"
