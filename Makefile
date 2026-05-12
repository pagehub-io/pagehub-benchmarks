.PHONY: install test lint fmt run dry-run list site clean

PY ?= python3
BENCHMARK ?= chess-backend
PYTEST_ARGS ?=

install:
	$(PY) -m pip install -r requirements.txt

test:
	$(PY) -m pytest $(PYTEST_ARGS)

lint:
	$(PY) -m ruff check .

fmt:
	$(PY) -m ruff check --fix .

# Run a benchmark. Pass DRY_RUN=1 for the offline wiring sanity-check
# (no harness, no pagehub-evals). Extra flags: ARGS="--harness claude-code".
run:
ifeq ($(DRY_RUN),1)
	$(PY) -m pagehub_benchmarks run $(BENCHMARK) --dry-run $(ARGS)
else
	$(PY) -m pagehub_benchmarks run $(BENCHMARK) $(ARGS)
endif

dry-run:
	$(PY) -m pagehub_benchmarks run $(BENCHMARK) --dry-run $(ARGS)

list:
	$(PY) -m pagehub_benchmarks list

# Regenerate the static results site into docs/ from results/**/*.json.
site:
	$(PY) -m tools.build_site

clean:
	rm -rf .worktrees .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
