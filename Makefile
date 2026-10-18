PYTHON := python
PORT   ?= 8000
export PORT

.DEFAULT_GOAL := help
.PHONY: help install check data dev start test clean venv

help:  ## Show commands
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-9s %s\n", $$1, $$2}'

venv:  ## Create .venv (then: . .venv/bin/activate && make install)
	$(PYTHON) -m venv .venv

install:  ## Install dependencies from requirements.txt
	$(PYTHON) -m pip install -r requirements.txt

check:  ## Verify Python, dependencies, files, dataset, port
	$(PYTHON) scripts/preflight.py --port $(PORT)

data:  ## (Re)generate synthetic dataset + manifests
	$(PYTHON) scripts/make_datasets.py

dev:  ## Preflight, then run backend + frontend together (auto-reload) on PORT (default 8000)
	$(PYTHON) scripts/dev.py --port $(PORT)

start:  ## Same as dev but without auto-reload
	$(PYTHON) scripts/dev.py --port $(PORT) --no-reload

test:  ## Run the full test suite
	$(PYTHON) scripts/preflight.py --skip-port && $(PYTHON) -m pytest backend/tests -q

clean:  ## Remove caches and local runtime data (uploaded-document records)
	rm -rf .pytest_cache data/runtime && find . -name __pycache__ -prune -exec rm -rf {} +
