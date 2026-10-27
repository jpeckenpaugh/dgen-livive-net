# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/cache/huggingface \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        espeak-ng \
        ffmpeg \
        libgomp1 \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# CPU-only torch. The default PyPI wheel for Linux pulls the CUDA runtime,
# which adds several gigabytes to an image that never uses a GPU.
ARG TORCH_VERSION=2.14.1+cpu
RUN pip install --no-cache-dir \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        "torch==${TORCH_VERSION}"

# misaki[ja] pulls the `unidic` package, whose ~1 GB dictionary is fetched on
# first use (`python -m unidic download`). Remove it so fugashi falls back to
# the self-contained `unidic-lite` wheel instead. This keeps Japanese G2P
# offline-friendly and the image small.
RUN pip install --no-cache-dir -r requirements.txt \
    && pip uninstall -y unidic

RUN pip install --no-cache-dir \
        "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

COPY config ./config
COPY scripts ./scripts
COPY web ./web
COPY inputs.json .

# Force the pyopenjtalk dictionary download into site-packages at image build
# time (it is otherwise fetched lazily on first Japanese synthesis), and fail
# the build if Japanese or Mandarin phonemization is broken.
RUN python scripts/warmup_g2p.py

# The Kokoro model and voice packs are downloaded on first use into
# HF_HOME. docker-compose mounts that directory from ./.cache/huggingface so
# rebuilding the image does not force a model download.

ENTRYPOINT ["python", "scripts/build.py"]
CMD ["--out", "/out"]
