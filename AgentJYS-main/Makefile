.PHONY: install proto test lint typecheck engine-test check demo demo-tui up dashboard dashboard-down dashboard-logs down clean

install:
	uv sync --group dev --group demo

proto:
	uv run python scripts/generate_proto.py

test:
	uv run pytest

lint:
	uv run ruff check src tests scripts

typecheck:
	uv run mypy src

engine-test:
	cargo test --manifest-path engine/Cargo.toml --workspace

check: lint typecheck test engine-test

demo:
	uv run python examples/unified_demo.py

demo-tui:
	uv run python examples/unified_demo_tui.py

up:
	docker compose up --build

dashboard:
	docker compose up --build

dashboard-down:
	docker compose down --remove-orphans

dashboard-logs:
	docker compose logs --tail=200 p3 engine

down:
	docker compose down --remove-orphans

clean:
	uv cache clean
