# Convenience targets. The engine needs no dependencies; `make dev` adds the
# test/lint/PNG tooling.
.PHONY: help dev build test lint validate sweep dashboard betspread strategy clean

ROUNDS ?= 10000000
CORES  ?= 0
SEED   ?= 12345

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

dev:        ## install dev/CI dependencies
	pip install -r requirements-dev.txt

build:      ## compile the optional native (Cython) fast engine in place
	python setup.py build_ext --inplace

test:       ## run the test suite
	pytest

lint:       ## static lint (ruff)
	ruff check .

validate:   ## cross-check the strategy tables against the analytic EV calculator
	python validate_strategy.py
	python validate_strategy.py --h17

sweep:      ## run the full comparison matrix -> results/sweep.json
	python run_sim.py sweep --rounds $(ROUNDS) --cores $(CORES) --seed $(SEED)

betspread:  ## bet-spread breakeven search at two penetrations -> results/betspread_*.json
	python betspread.py --pen 0.75  --rounds $(ROUNDS) --cores $(CORES) --out results/betspread_pen75.json
	python betspread.py --pen 0.833 --rounds $(ROUNDS) --cores $(CORES) --out results/betspread_pen83.json

strategy:   ## generate strategy-panel data -> results/strategy_ev.json + strategy_chart.json
	python strategy_ev.py --out results/strategy_ev.json
	python strategy_chart.py --out results/strategy_chart.json

dashboard:  ## (re)build results/dashboard.html from existing results JSON
	python visualize.py --data results/sweep.json

clean:      ## remove generated dashboard/PNG outputs, native build, and caches
	rm -f results/dashboard.html results/*.png
	rm -f blackjack/_fastsim.c blackjack/*.so
	rm -rf build
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache
