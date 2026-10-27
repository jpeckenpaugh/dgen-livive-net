SHELL := /bin/bash
OUT ?= dist
PORT ?= 8080
VOICES ?=
WORKERS ?= 4

.PHONY: build build-parallel rebuild reproducible build-one prefetch validate serve shell clean

build:
	docker compose build
	docker compose run --rm builder --out /out

build-parallel:
	docker compose build
	python3 scripts/parallel_build.py --out "$(OUT)" --workers "$(WORKERS)" --prefetch

rebuild:
	docker compose run --rm builder --out /out --force

reproducible:
	docker compose run --rm builder --out /out --reproducible --force

build-one:
	docker compose run --rm builder --out /out --voices "$(VOICES)"

prefetch:
	docker compose run --rm builder --out /out --prefetch

validate:
	python3 scripts/validate.py "$(OUT)"

serve:
	python3 scripts/serve.py "$(OUT)" --port "$(PORT)"

shell:
	docker compose run --rm --entrypoint bash builder

clean:
	rm -rf "$(OUT)"
