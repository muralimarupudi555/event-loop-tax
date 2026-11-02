.PHONY: install lint test test-fast bench figures receipts verify clean

# Every target runs through one interpreter. A bare pytest resolves to whichever console
# script is first on PATH, which can belong to a different Python than the one holding the
# editable install, and the resulting ModuleNotFoundError points at a missing dependency
# when the cause is PATH resolution order.
PYTHON ?= python3

install:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .

test:
	$(PYTHON) -m pytest --cov=event_loop_tax --cov-report=term-missing --cov-fail-under=100

# Skips the tests that start a real uvicorn.
test-fast:
	$(PYTHON) -m pytest -m "not slow"

# Rerun the measurements and rewrite docs/experiments. Not part of verify: load timings
# are not reproducible, and a check that demands they match fails at random. See ADR-004.
bench:
	$(PYTHON) experiments/e1_four_shapes.py
	$(PYTHON) experiments/e2_concurrency_sweep.py
	$(PYTHON) experiments/e3_how_little_is_too_much.py
	$(PYTHON) experiments/e4_three_clocks.py

figures:
	$(PYTHON) tools/render_diagram.py
	$(PYTHON) tools/capture_cli.py

receipts:
	$(PYTHON) tools/check_invariant.py
	$(PYTHON) tools/collect_metrics.py
	$(PYTHON) tools/check_numbers.py

verify: lint test receipts

clean:
	rm -rf .pytest_cache .ruff_cache htmlcov coverage.json junit.xml .coverage
