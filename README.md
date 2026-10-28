# dgen-livive-net

Docker + GitHub Actions bake of pre-rendered Kokoro voice lines. Every phrase × voice → web-optimized Opus + a JSON registry, published to GitHub Pages.

## What is built

- **54 voices** — 28 English (20 US, 8 UK), 3 Spanish, 3 Brazilian Portuguese, 4 Hindi, 1 French, 2 Italian, 5 Japanese, and 8 Mandarin Chinese.
- **26 phrases** authored in English and translated into Spanish, Brazilian Portuguese, Hindi, French, Italian, Japanese, and Mandarin Chinese.
- **1,404 clips** in total: 728 English + 78 Spanish + 78 Portuguese + 104 Hindi + 26 French + 52 Italian + 130 Japanese + 208 Mandarin Chinese.
- **Opus 32 kbps mono** in a WebM container with a `.weba` extension.
  - `.weba` is intentional: GitHub Pages serves it as `audio/webm`, while `.webm` would be `video/webm`.
- Audio is normalized, trimmed, and written with plain filenames. `dist/` is a build artifact and is not committed.

## Layout

```text
inputs.json              phrases, translations, groups, and hooks
config/voices.json       included voices, accents, locales, language codes, and optional grades
config/encode.json       Opus/WebM encoding settings
scripts/build.py         synthesis + ffmpeg encoding + registry generation
scripts/parallel_build.py runs voice shards concurrently and merges them
scripts/merge.py         combines shard audio, registries, and clip caches
scripts/validate.py      checks registry/file counts, sizes, hashes, totals, language coverage, and hooks
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

Rebuilding the Docker image therefore does not re-download the model. The first image build and first model/voice prefetch need network access. Once the cache is populated, subsequent builds can run offline; adding a new voice or model requires network access again.

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

The image is large because it contains PyTorch, build tooling, and Japanese/Mandarin G2P dictionaries. The Kokoro model itself is not baked into the image.

## Commands

| Command | Purpose |
|---|---|
| `make build` | Incremental single-container build. Reuses existing clips. |
| `make build-parallel WORKERS=4` | Prefetches voices, builds parallel shards, and merges into `dist/`. |
| `make prefetch` | Downloads the model and all configured voice packs without generating audio. |
| `make build-one VOICES=af_heart` | Builds or reuses one voice. Rewrites the registry for that selection. |
| `make rebuild` | Forces regeneration of all selected clips. |
| `make reproducible` | Forces a seeded, single-threaded rebuild with stable output hashes. |
| `make validate` | Validates the generated registry and files. Orphans are warnings unless `--strict` is passed. |
| `make serve` | Serves `dist/` at `http://127.0.0.1:8080/` with the correct `.weba` MIME type. |
| `make shell` | Opens a shell in the builder image. |
| `make clean` | Removes `dist/` but leaves the model cache. |

`make build-one` leaves other voices' audio in `dist/` while rewriting `registry.json` for the selected voice(s). Run `make clean` first, or invoke the builder with `--out` pointing at a separate directory, if you need a pristine single-voice tree.

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
- Existing clips are reused by default. A clip is regenerated only if its file is missing, corrupt, or its `clipKey` changes (text, voice, speed, model, encoder, or processing version).
- A hidden `dist/.clip-cache.json` keeps clip fingerprints across partial builds, so `make build-one` followed by `make build` still reuses the unchanged files.
- `make build` is incremental; if every selected clip is reusable, the model is not loaded at all.
- `make build-parallel` seeds each shard from the current output, runs several Docker shards concurrently, and merges their audio, registries, and clip caches. The Pages workflow uses four shards and allocates CPU threads per shard.
- `make rebuild` regenerates selected clips (`--force`).
- Normal builds use all available CPU threads and do not seed per-clip randomness.
- `--reproducible` seeds each clip and defaults to one thread; `make reproducible` forces a repeatable rebuild.

## Data Contracts

### `inputs.json`

`version` is currently `4`; `sourceLanguage` is `en`.

```json
{
  "version": 4,
  "sourceLanguage": "en",
  "groups": ["greeting", "friendship"],
  "phrases": [
    {
      "id": "greet",
      "text": "Hi there!",
      "group": "greeting",
      "translations": {
        "es": "¡Hola!",
        "pt": "Oi!",
        "hi": "नमस्ते!",
        "fr": "Salut !",
        "it": "Ciao!",
        "ja": "やあ！",
        "zh": "嗨！"
      }
    }
  ],
  "hooks": {
    "stranger": ["greet", "be-friends"]
  }
}
```

- `text` is the English source. `translations` must cover every configured non-source language.
- `alternate` is optional and marks extra phrase variants.
- Every hook value must reference an existing phrase ID.

### `config/voices.json`

```json
{
  "default": "af_heart",
  "voices": [
    {
      "id": "af_heart",
      "accent": "en-US",
      "locale": "en-US",
      "country": "United States",
      "gender": "female",
      "lang": "a",
      "language": "en",
      "grade": "A"
    }
  ]
}
```

- `lang` is the Kokoro G2P language code.
- `language` selects the phrase translation.
- `locale` and `country` drive the test-page filters and are display metadata.
- `grade` is optional, English-only, and is Kokoro's training-data estimate. It is not an intelligibility or voice-quality score.
- The Spanish voices use `country: "Spanish (region unspecified)"` rather than inventing a region.

### `registry.json`

Generated by the build; do not hand-edit. It contains:

- `schemaVersion`
- `build` metadata: model, encoder, pipeline, totals, build/source IDs
- `sourceLanguage`
- `languages`
- `defaultVoice`
- `voices`
- `groups`
- `phrases`
- `hooks`
- `files`: per-voice, per-phrase entries with `path`, `bytes`, `durationMs`, `sha256`, `phonemes`, and `clipKey`

### `.clip-cache.json`

Private incremental-build manifest. It is not part of the public Pages artifact and should not be edited manually.

## Test UI

`make serve` opens the generated test page. It provides:

- voice filtering by language, gender, locale/region, and an advanced source-grade filter
- counts for each filter option
- OR logic within a filter category and AND logic across categories
- voices grouped by language and alphabetized by ID
- automatic fallback to the first visible voice when filters hide the current selection
- a zero-result empty state and a “Clear filters” action
- language-specific phrase text, with the English source shown beneath non-English lines
- a hook tester that selects a random phrase for the chosen hook and voice

The source-grade filter is under “Training-data estimate” and is English-only. It lists the available values plus `Unrated` for voices with no published grade. Grade is never shown in the voice label.

## Included voices

All 54 Kokoro v1.0 voices are included. `af_heart` remains the default.

| Language / accent | Female | Male |
|---|---|---|
| US English | `af_heart`, `af_bella`, `af_nicole`, `af_aoede`, `af_alloy`, `af_jessica`, `af_kore`, `af_nova`, `af_river`, `af_sarah`, `af_sky` | `am_fenrir`, `am_michael`, `am_puck`, `am_onyx`, `am_adam`, `am_echo`, `am_eric`, `am_liam`, `am_santa` |
| UK English | `bf_emma`, `bf_isabella`, `bf_alice`, `bf_lily` | `bm_fable`, `bm_george`, `bm_lewis`, `bm_daniel` |
| Spanish | `ef_dora` | `em_alex`, `em_santa` |
| Brazilian Portuguese | `pf_dora` | `pm_alex`, `pm_santa` |
| Hindi | `hf_alpha`, `hf_beta` | `hm_omega`, `hm_psi` |
| French | `ff_siwis` | — |
| Italian | `if_sara` | `im_nicola` |
| Japanese | `jf_alpha`, `jf_gongitsune`, `jf_nezumi`, `jf_tebukuro` | `jm_kumo` |
| Mandarin Chinese | `zf_xiaobei`, `zf_xiaoni`, `zf_xiaoxiao`, `zf_xiaoyi` | `zm_yunjian`, `zm_yunxi`, `zm_yunxia`, `zm_yunyang` |

Kokoro is weakest on very short utterances; these lines are 1–6 words, so audition before treating the bake as final. Non-English support is thinner than English: Spanish, Portuguese, Hindi, French, and Italian use eSpeak NG fallback phonemization, while Japanese and Mandarin use `misaki` G2P dictionaries. Check all non-English lines carefully, especially Japanese kanji readings/pitch accent and Mandarin tone sandhi, polyphones, and punctuation pauses.
