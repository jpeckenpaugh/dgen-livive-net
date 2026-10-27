#!/usr/bin/env python3
"""Bake G2P dictionaries and verify Japanese/Mandarin phonemization.

Run during the Docker image build. `misaki[ja]` uses cutlet, which reads the
bundled `unidic-lite` dictionary, but the package still imports pyopenjtalk.
Importing pyopenjtalk is cheap; its open_jtalk dictionary is only downloaded
when the pyopenjtalk backend is actually used. Force that download here so a
runtime build never needs to fetch it.
"""

from __future__ import annotations

import os

import pyopenjtalk
from misaki import ja, zh

JAPANESE_SAMPLE = "こんにちは！"
CHINESE_SAMPLE = "你好！"


def main() -> None:
    pyopenjtalk._lazy_init()  # noqa: SLF001 - pyopenjtalk has no public warmup
    dictionary = pyopenjtalk.OPEN_JTALK_DICT_DIR
    if isinstance(dictionary, bytes):
        dictionary = dictionary.decode("utf-8")
    if not os.path.isdir(dictionary):
        raise SystemExit(f"pyopenjtalk dictionary missing: {dictionary}")

    japanese = ja.JAG2P()
    japanese_phonemes, _ = japanese(JAPANESE_SAMPLE)
    if not japanese_phonemes:
        raise SystemExit("Japanese G2P returned no phonemes")

    chinese = zh.ZHG2P()
    chinese_phonemes, _ = chinese(CHINESE_SAMPLE)
    if not chinese_phonemes:
        raise SystemExit("Mandarin G2P returned no phonemes")

    print(f"pyopenjtalk dictionary: {dictionary}")
    print(f"ja: {JAPANESE_SAMPLE} -> {japanese_phonemes}")
    print(f"zh: {CHINESE_SAMPLE} -> {chinese_phonemes}")


if __name__ == "__main__":
    main()
