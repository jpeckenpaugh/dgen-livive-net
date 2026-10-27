#!/usr/bin/env python3
"""Build the pre-rendered Kokoro voice pack and the browser test page."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from kokoro import KModel, KPipeline

ROOT = Path(__file__).resolve().parents[1]
MODEL_REPO = "hexgrad/Kokoro-82M"
MODEL_RELEASE = "v1.0"
MODEL_SHA256 = "496dba118d1a58f5f3db2efc88dbdc216e0483fc89fe6e47ee1f2c53f18ad1e4"
SOURCE_RATE = 24000
TARGET_PEAK = 10 ** (-1.5 / 20)  # -1.5 dBFS
CLIP_VERSION = 1  # bump when audio processing changes
CLIP_CACHE_FILE = ".clip-cache.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--voices",
        default="",
        help="Comma-separated voice IDs to build (default: all in config/voices.json)",
    )
    parser.add_argument(
        "--phrases",
        default="",
        help="Comma-separated phrase IDs to build (default: all in inputs.json)",
    )
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate selected clips even when a reusable file exists",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing output files before building",
    )
    parser.add_argument(
        "--prefetch",
        action="store_true",
        help="Download the model and selected voice packs into the cache, then exit",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="Torch CPU threads (0 = use TORCH_NUM_THREADS or 1)",
    )
    return parser.parse_args()


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"missing {path}")
    except json.JSONDecodeError as exc:
        fail(f"invalid JSON in {path}: {exc}")


def csv_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def validate_inputs(inputs: dict, voice_config: dict) -> None:
    groups = inputs.get("groups")
    phrases = inputs.get("phrases")
    hooks = inputs.get("hooks")
    if not isinstance(groups, list) or not groups:
        fail("inputs.json: groups must be a non-empty list")
    if not isinstance(phrases, list) or not phrases:
        fail("inputs.json: phrases must be a non-empty list")
    if not isinstance(hooks, dict) or not hooks:
        fail("inputs.json: hooks must be a non-empty object")

    group_set = set(groups)
    phrase_ids: list[str] = []
    for phrase in phrases:
        if not isinstance(phrase, dict):
            fail("inputs.json: every phrase must be an object")
        for key in ("id", "text", "group"):
            if not phrase.get(key):
                fail(f"inputs.json: phrase missing {key}: {phrase!r}")
        phrase_id = phrase["id"]
        if not all(part and part.replace("-", "").isalnum() for part in phrase_id.split("-")):
            fail(f"inputs.json: unsafe phrase id {phrase_id!r}")
        if phrase["group"] not in group_set:
            fail(f"inputs.json: phrase {phrase_id!r} uses unknown group {phrase['group']!r}")
        phrase_ids.append(phrase_id)

    if len(set(phrase_ids)) != len(phrase_ids):
        fail("inputs.json: duplicate phrase IDs")

    phrase_set = set(phrase_ids)
    for hook, ids in hooks.items():
        if not isinstance(ids, list) or not ids:
            fail(f"inputs.json: hook {hook!r} must be a non-empty list")
        missing = sorted(set(ids) - phrase_set)
        if missing:
            fail(f"inputs.json: hook {hook!r} references unknown phrases: {missing}")

    voices = voice_config.get("voices")
    if not isinstance(voices, list) or not voices:
        fail("config/voices.json: voices must be a non-empty list")
    voice_ids: list[str] = []
    for voice in voices:
        for key in ("id", "accent", "gender", "lang"):
            if not voice.get(key):
                fail(f"config/voices.json: voice missing {key}: {voice!r}")
        voice_id = voice["id"]
        prefix, _, _ = voice_id.partition("_")
        if prefix not in {"af", "am", "bf", "bm"}:
            fail(f"config/voices.json: unsupported English voice {voice_id!r}")
        expected_lang = "a" if prefix in {"af", "am"} else "b"
        if voice["lang"] != expected_lang:
            fail(
                f"config/voices.json: {voice_id!r} should use lang {expected_lang!r}, "
                f"not {voice['lang']!r}"
            )
        if voice["gender"] not in {"female", "male"}:
            fail(f"config/voices.json: {voice_id!r} has invalid gender")
        voice_ids.append(voice_id)

    if len(set(voice_ids)) != len(voice_ids):
        fail("config/voices.json: duplicate voice IDs")
    if voice_config.get("default") not in set(voice_ids):
        fail("config/voices.json: default must name a configured voice")


def select_items(items: list[dict], requested: list[str], kind: str) -> list[dict]:
    if not requested:
        return items
    by_id = {item["id"]: item for item in items}
    unknown = sorted(set(requested) - set(by_id))
    if unknown:
        fail(f"unknown {kind}: {unknown}")
    wanted = set(requested)
    return [item for item in items if item["id"] in wanted]


def trim_silence(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 0.0:
        fail("synthesized audio is silent")
    threshold = max(peak * 10 ** (-45 / 20), 1e-4)
    indices = np.flatnonzero(np.abs(audio) >= threshold)
    if indices.size == 0:
        return audio
    pad = int(sample_rate * 0.04)
    start = max(0, int(indices[0]) - pad)
    end = min(len(audio), int(indices[-1]) + pad + 1)
    return audio[start:end]


def normalize_audio(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)
    audio = trim_silence(audio, sample_rate)

    fade = min(int(sample_rate * 0.005), len(audio) // 2)
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
        audio[:fade] *= ramp
        audio[-fade:] *= ramp[::-1]

    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak <= 0.0:
        fail("synthesized audio is silent after trimming")
    return audio * (TARGET_PEAK / peak)


def clip_seed(voice_id: str, phrase_id: str, speed: float) -> int:
    """Stable per-clip seed.

    Kokoro's ISTFTNet source module uses random initial phase and noise, so a
    seed is required for repeatable builds and stable content hashes.
    """
    material = f"{MODEL_SHA256}|{voice_id}|{phrase_id}|{speed:.6f}"
    return (
        int.from_bytes(hashlib.sha256(material.encode("utf-8")).digest()[:8], "big")
        % (2**32 - 1)
    )


def synthesize(
    pipeline: KPipeline, text: str, voice: str, speed: float
) -> tuple[np.ndarray, str]:
    chunks: list[np.ndarray] = []
    phonemes: list[str] = []
    for result in pipeline(text, voice=voice, speed=speed):
        if result.audio is None:
            continue
        chunk = result.audio.detach().cpu().numpy().astype(np.float32).reshape(-1)
        if chunk.size:
            chunks.append(chunk)
        if result.phonemes:
            phonemes.append(result.phonemes)
    if not chunks:
        raise RuntimeError(f"no audio returned for {text!r}")
    return np.concatenate(chunks), " ".join(phonemes).strip()


def encode_opus(wav_path: Path, out_path: Path, encoder: dict) -> None:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        str(wav_path),
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:a",
        "+bitexact",
        "-ar",
        str(encoder["sampleRate"]),
        "-ac",
        str(encoder["channels"]),
        "-c:a",
        encoder["codec"],
        "-b:a",
        encoder["bitrate"],
        "-vbr",
        encoder["vbr"],
        "-compression_level",
        str(encoder["compressionLevel"]),
        "-frame_duration",
        str(encoder["frameDuration"]),
        "-application",
        encoder["application"],
        "-f",
        encoder["container"],
        str(out_path),
    ]
    subprocess.run(command, check=True)


def probe_duration_ms(path: Path) -> int:
    output = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        text=True,
    ).strip()
    return int(round(float(output) * 1000))


def relative_clip_path(voice_id: str, phrase_id: str, encoder: dict) -> str:
    return (
        Path("audio") / voice_id / f"{phrase_id}{encoder['extension']}"
    ).as_posix()


def clip_key(phrase: dict, voice: dict, speed: float, encoder: dict) -> str:
    """Fingerprint everything that affects the bytes of one clip."""
    payload = {
        "version": CLIP_VERSION,
        "text": phrase["text"],
        "voice": voice["id"],
        "accent": voice["accent"],
        "lang": voice["lang"],
        "speed": round(float(speed), 6),
        "model": MODEL_SHA256,
        "encoder": encoder,
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def load_previous_registry(out_dir: Path) -> dict | None:
    path = out_dir / "registry.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_clip_cache(out_dir: Path) -> dict:
    path = out_dir / CLIP_CACHE_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    return files if isinstance(files, dict) else {}


def merge_clip_cache(cache: dict, previous: dict | None) -> dict:
    if previous and isinstance(previous.get("files"), dict):
        for voice_id, entries in previous["files"].items():
            if not isinstance(entries, dict):
                continue
            cache.setdefault(voice_id, {}).update(entries)
    return cache


def write_clip_cache(out_dir: Path, cache: dict) -> None:
    # Drop entries whose files disappeared, so the cache is self-cleaning.
    pruned: dict[str, dict[str, dict]] = {}
    for voice_id, entries in cache.items():
        for phrase_id, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            if isinstance(path, str) and (out_dir / path).is_file():
                pruned.setdefault(voice_id, {})[phrase_id] = entry

    (out_dir / CLIP_CACHE_FILE).write_text(
        json.dumps({"version": 1, "files": pruned}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )


def clear_directory(directory: Path) -> None:
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def reusable_entry(
    out_dir: Path,
    previous: dict | None,
    voice: dict,
    phrase: dict,
    speed: float,
    encoder: dict,
) -> dict | None:
    if not previous:
        return None

    previous_files = previous.get("files")
    if not isinstance(previous_files, dict):
        return None
    previous_entry = previous_files.get(voice["id"], {}).get(phrase["id"])
    if not isinstance(previous_entry, dict):
        return None

    previous_path = previous_entry.get("path")
    if not isinstance(previous_path, str):
        return None
    path = out_dir / previous_path
    if not path.is_file():
        return None

    key = clip_key(phrase, voice, speed, encoder)
    previous_key = previous_entry.get("clipKey")
    if previous_key:
        if previous_key != key:
            return None
    else:
        # Compatibility with registries written before per-clip fingerprints.
        previous_build = previous.get("build", {})
        previous_phrase = next(
            (
                item
                for item in previous.get("phrases", [])
                if item.get("id") == phrase["id"]
            ),
            None,
        )
        previous_voice = next(
            (
                item
                for item in previous.get("voices", [])
                if item.get("id") == voice["id"]
            ),
            None,
        )
        if previous_phrase is None or previous_voice is None:
            return None
        if previous_phrase.get("text") != phrase["text"]:
            return None
        for field in ("accent", "gender", "lang"):
            if previous_voice.get(field) != voice.get(field):
                return None
        if previous_build.get("pipeline", {}).get("speed") != speed:
            return None
        if previous_build.get("encoder") != encoder:
            return None
        if previous_build.get("model", {}).get("sha256") != MODEL_SHA256:
            return None

    data = path.read_bytes()
    if previous_entry.get("bytes") not in (None, len(data)):
        return None
    digest = hashlib.sha256(data).hexdigest()
    if previous_entry.get("sha256") and previous_entry["sha256"] != digest:
        return None

    expected_path = relative_clip_path(voice["id"], phrase["id"], encoder)
    destination = out_dir / expected_path
    if path != destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        try:
            path.unlink()
        except OSError:
            pass

    duration_ms = previous_entry.get("durationMs")
    if not isinstance(duration_ms, int) or duration_ms <= 0:
        duration_ms = probe_duration_ms(destination)

    return {
        "path": expected_path,
        "bytes": len(data),
        "durationMs": duration_ms,
        "sha256": digest,
        "phonemes": previous_entry.get("phonemes", ""),
        "clipKey": key,
    }


def make_pipelines(languages: set[str]) -> dict[str, KPipeline]:
    model = KModel(repo_id=MODEL_REPO).to("cpu").eval()
    return {
        lang: KPipeline(lang_code=lang, model=model, repo_id=MODEL_REPO)
        for lang in sorted(languages)
    }


def make_registry(
    inputs: dict,
    voices: list[dict],
    phrases: list[dict],
    files: dict,
    encoder: dict,
    speed: float,
    source_hash: str,
    default_voice: str,
    elapsed_seconds: float,
    threads: int,
    reused: int,
    generated: int,
) -> dict:
    clip_count = sum(len(entries) for entries in files.values())
    total_bytes = sum(
        entry["bytes"] for entries in files.values() for entry in entries.values()
    )
    total_ms = sum(
        entry["durationMs"] for entries in files.values() for entry in entries.values()
    )

    return {
        "schemaVersion": 1,
        "build": {
            "id": source_hash[:12],
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sourceHash": source_hash,
            "clipVersion": CLIP_VERSION,
            "elapsedSeconds": round(elapsed_seconds, 3),
            "model": {
                "repo": MODEL_REPO,
                "release": MODEL_RELEASE,
                "sha256": MODEL_SHA256,
            },
            "pipeline": {
                "name": "kokoro",
                "version": package_version("kokoro"),
                "misakiVersion": package_version("misaki"),
                "torchVersion": torch.__version__,
                "speed": speed,
                "threads": threads,
            },
            "encoder": encoder,
            "totals": {
                "clips": clip_count,
                "voices": len(files),
                "phrases": len(phrases),
                "bytes": total_bytes,
                "audioMs": total_ms,
                "audioSeconds": round(total_ms / 1000, 3),
                "reused": reused,
                "generated": generated,
            },
        },
        "defaultVoice": default_voice,
        "voices": voices,
        "groups": inputs["groups"],
        "phrases": phrases,
        "hooks": inputs["hooks"],
        "files": files,
    }


def write_site(out_dir: Path, registry: dict) -> None:
    template_path = ROOT / "web" / "index.html"
    template = template_path.read_text(encoding="utf-8")
    if "__REGISTRY_JSON__" not in template:
        fail(f"{template_path} is missing the __REGISTRY_JSON__ placeholder")

    registry_json = json.dumps(
        registry, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    html = template.replace("__REGISTRY_JSON__", registry_json)

    (out_dir / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "index.html").write_text(html, encoding="utf-8")


def main() -> None:
    args = parse_args()
    started = time.monotonic()

    inputs = load_json(ROOT / "inputs.json")
    voice_config = load_json(ROOT / "config" / "voices.json")
    encoder = load_json(ROOT / "config" / "encode.json")
    validate_inputs(inputs, voice_config)

    voices = select_items(voice_config["voices"], csv_ids(args.voices), "voice")
    phrases = select_items(inputs["phrases"], csv_ids(args.phrases), "phrase")
    if not voices:
        fail("no voices selected")
    if not phrases:
        fail("no phrases selected")

    default_voice = voice_config["default"]
    if default_voice not in {voice["id"] for voice in voices}:
        default_voice = voices[0]["id"]

    threads = args.threads or int(os.environ.get("TORCH_NUM_THREADS", "1"))
    torch.set_grad_enabled(False)
    torch.set_num_threads(threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass

    if args.prefetch:
        print(f"prefetching {MODEL_REPO} {MODEL_RELEASE}", flush=True)
        pipelines = make_pipelines({voice["lang"] for voice in voices})
        for voice in voices:
            print(f"  {voice['id']}", flush=True)
            pipelines[voice["lang"]].load_voice(voice["id"])
        cache = os.environ.get("HF_HOME", "default Hugging Face cache")
        print(f"prefetched {len(voices)} voices into {cache}", flush=True)
        return

    out_dir = args.out.expanduser().resolve()
    if out_dir == Path("/"):
        fail("refusing to clean the filesystem root")
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.clean:
        clear_directory(out_dir)

    previous = load_previous_registry(out_dir)
    cache = merge_clip_cache(load_clip_cache(out_dir), previous)
    reuse_source = {
        "files": cache,
        "phrases": previous.get("phrases", []) if previous else [],
        "voices": previous.get("voices", []) if previous else [],
        "build": previous.get("build", {}) if previous else {},
    }

    source_payload = {
        "inputs": inputs,
        "voices": voice_config,
        "encoder": encoder,
        "speed": args.speed,
        "clipVersion": CLIP_VERSION,
        "selectedVoices": [voice["id"] for voice in voices],
        "selectedPhrases": [phrase["id"] for phrase in phrases],
        "model": {
            "repo": MODEL_REPO,
            "release": MODEL_RELEASE,
            "sha256": MODEL_SHA256,
        },
    }
    source_hash = hashlib.sha256(
        json.dumps(
            source_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()

    print(f"model: {MODEL_REPO} {MODEL_RELEASE}", flush=True)
    print(
        f"voices: {len(voices)}  phrases: {len(phrases)}  "
        f"clips: {len(voices) * len(phrases)}",
        flush=True,
    )

    files: dict[str, dict[str, dict]] = {}
    jobs: list[tuple[dict, dict]] = []
    reused = 0

    for voice in voices:
        voice_id = voice["id"]
        files[voice_id] = {}
        for phrase in phrases:
            entry = None
            if not args.force:
                entry = reusable_entry(
                    out_dir, reuse_source, voice, phrase, args.speed, encoder
                )
            if entry is None:
                jobs.append((voice, phrase))
            else:
                files[voice_id][phrase["id"]] = entry
                cache.setdefault(voice_id, {})[phrase["id"]] = entry
                reused += 1

    if jobs:
        print(
            f"reusing {reused} clip(s), generating {len(jobs)}",
            flush=True,
        )
        pipelines = make_pipelines({voice["lang"] for voice, _ in jobs})
        for index, (voice, phrase) in enumerate(jobs, 1):
            voice_id = voice["id"]
            phrase_id = phrase["id"]
            label = f"[{index:>3}/{len(jobs)}] {voice_id:<12} {phrase_id:<18}"
            print(label, end="", flush=True)

            seed = clip_seed(voice_id, phrase_id, args.speed)
            torch.manual_seed(seed)
            random.seed(seed)
            np.random.seed(seed)
            audio, phonemes = synthesize(
                pipelines[voice["lang"]], phrase["text"], voice_id, args.speed
            )
            audio = normalize_audio(audio, SOURCE_RATE)

            with tempfile.TemporaryDirectory(prefix="dgen-") as temp_dir:
                wav_path = Path(temp_dir) / "clip.wav"
                encoded_path = Path(temp_dir) / "clip.weba"
                sf.write(wav_path, audio, SOURCE_RATE, subtype="PCM_16")
                encode_opus(wav_path, encoded_path, encoder)
                data = encoded_path.read_bytes()

            digest = hashlib.sha256(data).hexdigest()
            relative_path = relative_clip_path(voice_id, phrase_id, encoder)
            destination = out_dir / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)

            duration_ms = probe_duration_ms(destination)
            entry = {
                "path": relative_path,
                "bytes": len(data),
                "durationMs": duration_ms,
                "sha256": digest,
                "phonemes": phonemes,
                "clipKey": clip_key(phrase, voice, args.speed, encoder),
            }
            files[voice_id][phrase_id] = entry
            cache.setdefault(voice_id, {})[phrase_id] = entry
            print(
                f" -> {len(data) / 1024:5.1f} KB  {duration_ms:4d} ms",
                flush=True,
            )
    else:
        print(f"reusing all {reused} clip(s); model not loaded", flush=True)

    elapsed = time.monotonic() - started
    registry = make_registry(
        inputs,
        voices,
        phrases,
        files,
        encoder,
        args.speed,
        source_hash,
        default_voice,
        elapsed,
        threads,
        reused,
        len(jobs),
    )
    write_site(out_dir, registry)
    write_clip_cache(out_dir, cache)

    totals = registry["build"]["totals"]
    print(
        f"\nbuilt {totals['clips']} clips "
        f"({reused} reused, {len(jobs)} generated), "
        f"{totals['bytes'] / 1024 / 1024:.2f} MiB, "
        f"{totals['audioSeconds']:.1f}s audio in {elapsed:.1f}s",
        flush=True,
    )
    print(f"output: {out_dir}", flush=True)
    print(f"test:   python3 scripts/serve.py {out_dir} --port 8080", flush=True)


if __name__ == "__main__":
    main()
