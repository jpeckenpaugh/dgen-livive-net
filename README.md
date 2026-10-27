# dgen-livive-net

Docker + GitHub Actions bake of pre-rendered Kokoro voice lines. Every phrase × voice → web-optimized Opus + a JSON registry, published to GitHub Pages.

## What is built

- **16 English voices** — 8 US, 8 UK, split 4 female / 4 male per accent.
- **26 phrases** from `inputs.json`, including the alternates.
- **416 clips** in total.
- **Opus 32 kbps mono** in a WebM container with a `.weba` extension.
  - `.weba` is intentional: GitHub Pages serves it as `audio/webm`, while `.webm` would be `video/webm`.
- Audio is normalized, trimmed, and written with plain filenames. `dist/` is a build artifact and is not committed.

## Layout

```text
inputs.json              phrases, groups, and hooks
config/voices.json       the selected 16 voices and their language codes
config/encode.json       Opus/WebM encoding settings
scripts/build.py         synthesis + ffmpeg encoding + registry generation
scripts/validate.py      checks registry/file counts, sizes, hashes, and hooks
scripts/serve.py         local static server with .weba MIME handling
web/index.html           test-page template (registry is injected at build time)
Dockerfile               pinned Kokoro pipeline, ffmpeg, espeak-ng; no model baked in
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

Output lands in `dist/`:

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

- Synthesis uses the official `kokoro` package with `misaki` G2P:
  - `af_*` / `am_*` → `lang_code='a'` (American English)
  - `bf_*` / `bm_*` → `lang_code='b'` (British English)
- Clips are trimmed, faded, and peak-normalized to −1.5 dBFS before encoding.
- Filenames are plain (`<phrase>.weba`); `registry.json` carries the per-clip SHA-256 if integrity checking is needed.
- `registry.json` includes build/model/encoder metadata, voice metadata, phrase and hook data, and per-clip path, size, duration, SHA-256, phonemes, and a `clipKey`.
- Existing clips are reused by default. A clip is regenerated only if its file is missing, corrupt, or its `clipKey` changes (text, voice, speed, model, encoder, or processing version).
- A hidden `dist/.clip-cache.json` keeps clip fingerprints across partial builds, so `make build-one` followed by `make build` still reuses the unchanged files.
- `make build` is incremental; if every selected clip is reusable, the model is not loaded at all.
- `make rebuild` regenerates all selected clips (`--force`).
- `make clean` removes `dist/` but leaves the model cache; `make shell` opens a shell in the builder image.
- `make validate` checks every registry entry. Unreferenced audio files from partial builds are warnings unless you pass `--strict`.

## Current default voices

| Accent | Female | Male |
|---|---|---|
| US | `af_heart`, `af_bella`, `af_nicole`, `af_aoede` | `am_fenrir`, `am_michael`, `am_puck`, `am_onyx` |
| UK | `bf_emma`, `bf_isabella`, `bf_alice`, `bf_lily` | `bm_fable`, `bm_george`, `bm_lewis`, `bm_daniel` |

Kokoro is weakest on very short utterances; these lines are 1–6 words, so audition before treating the bake as final.
