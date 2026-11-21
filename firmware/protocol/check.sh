#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
cargo test --quiet
cargo run --quiet --bin generate

APP=../../app
OUT=$(mktemp -d)
swiftc -o "$OUT/check" "$APP/MemoryGlasses/Glasses/GlassesProtocol.swift" "$APP/MemoryGlasses/Glasses/GlassesPackets.swift" \
  "$APP/ProtocolCheck/main.swift"
"$OUT/check" vectors.json
