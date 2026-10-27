# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/cache/huggingface \
    TOKENIZERS_PARALLELISM=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
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

RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir \
        "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

COPY config ./config
COPY scripts ./scripts
COPY web ./web
COPY inputs.json .

# The Kokoro model and voice packs are downloaded on first use into
# HF_HOME. docker-compose mounts that directory from ./.cache/huggingface so
# rebuilding the image does not force a model download.

ENTRYPOINT ["python", "scripts/build.py"]
CMD ["--out", "/out"]
