.DEFAULT_GOAL := help

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
NODE ?= node
HOST ?= 127.0.0.1
PORT ?= 4321
CITY ?= turin

.PHONY: help sync plan status build start restart stop dashboard-status logs test test-layout climate-history pages publish

help:
	@printf '%s\n' \
	  'make sync              Collect due weather data (respects caches and limits)' \
	  'make climate-history   Download missing historical climate batches once (budgeted)' \
	  'make plan              Show API usage plan; no requests' \
	  'make status            Show collector usage and schedules; no requests' \
	  'make start             Build and start the dashboard in the background' \
	  'make restart           Build, then restart the managed dashboard' \
	  'make stop              Stop the managed dashboard' \
	  'make dashboard-status  Show dashboard process status' \
	  'make logs              Follow dashboard logs (Ctrl-C exits the log viewer)' \
	  'make build             Build the frontend without starting or syncing' \
	  'make pages             Export a static GitHub Pages site offline' \
	  'make publish           Sync, build, then commit/push generated files to gh-pages' \
	  'make test              Run offline Python tests' \
	  'make test-layout       Build and check viewport fit in installed Chrome (offline)' \
	  'Options: CITY=turin HOST=127.0.0.1 PORT=4321 PYTHON=.venv/bin/python NODE=node'

sync:
	$(PYTHON) -m collector sync --city "$(CITY)"

plan:
	$(PYTHON) -m collector plan

status:
	$(PYTHON) -m collector status

build:
	npm run build

start: build
	HOST="$(HOST)" PORT="$(PORT)" NODE="$(NODE)" $(PYTHON) scripts/dashboard.py start

# Build must succeed before the existing process is stopped.
restart: build
	HOST="$(HOST)" PORT="$(PORT)" NODE="$(NODE)" $(PYTHON) scripts/dashboard.py restart

stop:
	$(PYTHON) scripts/dashboard.py stop

dashboard-status:
	$(PYTHON) scripts/dashboard.py status

logs:
	@mkdir -p data
	@touch data/dashboard.log
	tail -n 60 -f data/dashboard.log

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(NODE) --test tests/test_pages.mjs

test-layout: build
	$(NODE) scripts/check-layout.mjs

climate-history:
	$(PYTHON) -m collector climate-history --city "$(CITY)"

pages:
	$(PYTHON) scripts/publish_pages.py build --city "$(CITY)"

publish:
	$(PYTHON) scripts/publish_pages.py publish --city "$(CITY)"
