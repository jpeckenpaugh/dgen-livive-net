# dgen-livive-net

Docker + GitHub Actions bake of pre-rendered Kokoro voice lines. Every phrase × voice → web-optimized Opus + a JSON registry, published to GitHub Pages.

## What is built

- **42 voices** — 16 English (8 US, 8 UK), 3 Spanish, 3 Brazilian Portuguese, 4 Hindi, 1 French, 2 Italian, 5 Japanese, and 8 Mandarin.
- **26 phrases** from `inputs.json`, translated into English, Spanish, Brazilian Portuguese, Hindi, French, Italian, Japanese, and Mandarin.
- **1,092 clips** in total: 416 English + 78 Spanish + 78 Portuguese + 104 Hindi + 26 French + 52 Italian + 130 Japanese + 208 Mandarin.
- **Opus 32 kbps mono** in a WebM container with a `.weba` extension.
  - `.weba` is intentional: GitHub Pages serves it as `audio/webm`, while `.webm` would be `video/webm`.
- Audio is normalized, trimmed, and written with plain filenames. `dist/` is a build artifact and is not committed.

## Layout

```text
inputs.json              phrases, translations, groups, and hooks
config/voices.json       the selected 42 voices, languages, and language codes
config/encode.json       Opus/WebM encoding settings
scripts/build.py         synthesis + ffmpeg encoding + registry generation
scripts/parallel_build.py runs voice shards concurrently and merges them
scripts/merge.py         combines shard audio, registries, and clip caches
scripts/validate.py      checks registry/file counts, sizes, hashes, and hooks
scripts/serve.py         local static server with .weba MIME handling
scripts/warmup_g2p.py    build-time Japanese/Mandarin G2P check + dictionary warmup
web/index.html           test-page template (registry is injected at build time)
Dockerfile               pinned Kokoro pipeline, ffmpeg, espeak-ng; G2P dictionaries baked in, no model
docker-compose.yml       builder service; mounts ./dist and the Hugging Face cache
```

## Local build

The model and voice packs are downloaded into a host-mounted cache on first use:

```text
./.cache/huggingface  ->  /cache/huggingface  (HF_HOME)
```

That means rebuilding the Docker image does not re-download the model. The first `make build` needs network access; later runs can be offline from cache.

```bash
make build
```

For a faster full bake, build independent voice shards in parallel and merge them:

```bash
make build-parallel WORKERS=4
```

The GitHub Actions workflow uses this path automatically. Output lands in `dist/`:

```text
dist/
  audio/<voice>/<phrase>.weba
  registry.json
  index.html
  .clip-cache.json     # hidden incremental-build manifest
```

To download the model and voices without generating audio:

```bash
make prefetch
```

To try a single voice first:

```bash
make build-one VOICES=af_heart
```

To test in a browser:

```bash
make serve
# http://127.0.0.1:8080/
```

`scripts/serve.py` is required rather than `python -m http.server` because it sets the `.weba` content type to `audio/webm`. The generated `index.html` embeds the registry, so it also works when opened directly from disk, but serving it is the better check.

## Build behavior

- `inputs.json` stores the English source in `text` and translations in `translations` keyed by language code: `es`, `pt`, `hi`, `fr`, `it`, `ja`, and `zh`.
- Synthesis uses the official `kokoro` package:
  - `af_*` / `am_*` → `lang_code='a'` (American English, `misaki` G2P)
  - `bf_*` / `bm_*` → `lang_code='b'` (British English, `misaki` G2P)
  - `ef_*` / `em_*` → `lang_code='e'` (Spanish, eSpeak NG fallback)
  - `pf_*` / `pm_*` → `lang_code='p'` (Brazilian Portuguese, eSpeak NG fallback)
  - `hf_*` / `hm_*` → `lang_code='h'` (Hindi, eSpeak NG fallback)
  - `ff_*` → `lang_code='f'` (French, eSpeak NG fallback)
  - `if_*` / `im_*` → `lang_code='i'` (Italian, eSpeak NG fallback)
  - `jf_*` / `jm_*` → `lang_code='j'` (Japanese, `misaki` G2P via cutlet + UniDic Lite)
  - `zf_*` / `zm_*` → `lang_code='z'` (Mandarin Chinese, `misaki` G2P via jieba + pypinyin)
- Japanese uses the dictionary bundled in `unidic-lite`; the image removes the `unidic` package because its ~1 GB dictionary would otherwise be fetched at runtime. `scripts/warmup_g2p.py` bakes the pyopenjtalk dictionary into the image and fails the build if Japanese or Mandarin phonemization is broken.
- Clips are trimmed, faded, and peak-normalized to −1.5 dBFS before encoding.
- Filenames are plain (`<phrase>.weba`); `registry.json` carries the per-clip SHA-256 if integrity checking is needed.
- `registry.json` includes build/model/encoder metadata, voice metadata, source language, translations, phrase and hook data, and per-clip path, size, duration, SHA-256, phonemes, and a `clipKey`.
- Existing clips are reused by default. A clip is regenerated only if its file is missing, corrupt, or its `clipKey` changes (text, voice, speed, model, encoder, or processing version).
- A hidden `dist/.clip-cache.json` keeps clip fingerprints across partial builds, so `make build-one` followed by `make build` still reuses the unchanged files.
- `make build` is incremental; if every selected clip is reusable, the model is not loaded at all.
- `make build-parallel` seeds each shard from the current output, runs several Docker shards concurrently, and merges their audio, registries, and clip caches. The Pages workflow uses four shards.
- `make rebuild` regenerates all selected clips (`--force`).
- Normal builds are fast: all available CPU threads, no per-clip seeding.
- `--reproducible` seeds each clip and defaults to one thread; `make reproducible` forces a repeatable rebuild.
- `make clean` removes `dist/` but leaves the model cache; `make shell` opens a shell in the builder image.
- `make validate` checks every registry entry. Unreferenced audio files from partial builds are warnings unless you pass `--strict`.

## Current default voices

| Accent | Female | Male |
|---|---|---|
| US | `af_heart`, `af_bella`, `af_nicole`, `af_aoede` | `am_fenrir`, `am_michael`, `am_puck`, `am_onyx` |
| UK | `bf_emma`, `bf_isabella`, `bf_alice`, `bf_lily` | `bm_fable`, `bm_george`, `bm_lewis`, `bm_daniel` |
| Spanish | `ef_dora` | `em_alex`, `em_santa` |
| Brazilian Portuguese | `pf_dora` | `pm_alex`, `pm_santa` |
| Hindi | `hf_alpha`, `hf_beta` | `hm_omega`, `hm_psi` |
| French | `ff_siwis` | — |
| Italian | `if_sara` | `im_nicola` |
| Japanese | `jf_alpha`, `jf_gongitsune`, `jf_nezumi`, `jf_tebukuro` | `jm_kumo` |
| Mandarin Chinese | `zf_xiaobei`, `zf_xiaoni`, `zf_xiaoxiao`, `zf_xiaoyi` | `zm_yunjian`, `zm_yunxi`, `zm_yunxia`, `zm_yunyang` |

Kokoro is weakest on very short utterances; these lines are 1–6 words, so audition before treating the bake as final. Non-English support is thinner than English: Spanish, Portuguese, Hindi, French, and Italian use eSpeak NG fallback phonemization, while Japanese and Mandarin use `misaki` G2P dictionaries. Check all non-English lines carefully, especially Japanese kanji readings/pitch accent and Mandarin tone sandhi, polyphones, and punctuation pauses.
