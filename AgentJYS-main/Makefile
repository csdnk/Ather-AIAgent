.PHONY: install proto test lint typecheck engine-test check

install:
	python -m pip install -e '.[embedding-onnx,resource-documents,remember-ceph]' --group dev

proto:
	python -B scripts/generate_proto.py

test:
	python scripts/p3/run_aks_tests.py --environment-file "$(AETHER_TEST_ENV_FILE)" --assets-directory "$(AETHER_TEST_ASSETS)" --directory "$(AETHER_TEST_WORKSPACE)" $(AKS_TEST_ARGS) -- tests --ignore=tests/integration/test_current_p2.py

lint:
	python -B -m ruff check src tests scripts

typecheck:
	python -B -m mypy src

engine-test:
	cargo test --manifest-path engine/Cargo.toml --workspace

check: lint typecheck engine-test
