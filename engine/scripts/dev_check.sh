#!/usr/bin/env bash
set -euo pipefail

echo "[1/4] cargo fmt"
cargo fmt --all -- --check

echo "[2/4] cargo test"
cargo test --workspace

echo "[3/4] ae-cli smoke"
cargo run -p ae-cli

echo "[4/4] ae-server smoke"
cargo run -p ae-server

echo "AetherEngine dev check passed."
