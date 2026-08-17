param(
    [ValidateSet("test", "lint", "typecheck", "demo", "demo-tui", "proto", "engine-test", "dashboard")]
    [string]$Task = "test"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

switch ($Task) {
    "test" { uv run pytest }
    "lint" { uv run ruff check src tests scripts }
    "typecheck" { uv run mypy src }
    "demo" { uv run python examples/unified_demo.py }
    "demo-tui" { uv run python examples/unified_demo_tui.py }
    "proto" { uv run python scripts/generate_proto.py }
    "engine-test" { cargo test --manifest-path engine/Cargo.toml --workspace }
    "dashboard" { docker compose --profile dashboard up --build }
}
