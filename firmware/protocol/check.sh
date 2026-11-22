#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
cargo test --quiet
cargo run --quiet --bin generate

APP=../../app
BUILD_DIR=$(mktemp -d)
swiftc -o "$BUILD_DIR/check" "$APP/MemoryGlasses/Glasses/GlassesProtocol.swift" "$APP/MemoryGlasses/Glasses/GlassesPackets.swift" \
  "$APP/ProtocolCheck/main.swift"
"$BUILD_DIR/check" vectors.json
