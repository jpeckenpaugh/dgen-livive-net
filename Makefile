SHELL := /bin/bash
OUT ?= dist
PORT ?= 8080
VOICES ?=

.PHONY: build rebuild build-one prefetch validate serve shell clean

build:
	docker compose build
	docker compose run --rm builder --out /out

rebuild:
	docker compose run --rm builder --out /out --force

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
