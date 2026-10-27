#!/usr/bin/env python3
"""Merge independently generated voice shards into one complete dist directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {path}: {exc}")


def clear_directory(directory: Path) -> None:
    if not directory.exists():
        return
    if directory.resolve() == Path("/"):
        fail("refusing to clear the filesystem root")
    for child in directory.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def write_site(out_dir: Path, registry: dict) -> None:
    template = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    if "__REGISTRY_JSON__" not in template:
        fail("web/index.html is missing the registry placeholder")
    payload = json.dumps(
        registry, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    (out_dir / "index.html").write_text(
        template.replace("__REGISTRY_JSON__", payload), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    args = parser.parse_args()

    shards_root = args.shards.expanduser().resolve()
    out_dir = args.out.expanduser().resolve()
    registry_paths = sorted(shards_root.rglob("registry.json"))
    if not registry_paths:
        fail(f"no registry.json files found under {shards_root}")

    shard_dirs: list[Path] = []
    registries: list[dict] = []
    for path in registry_paths:
        shard_dirs.append(path.parent)
        registries.append(load_json(path))

    base = registries[0]
    phrase_ids = [phrase["id"] for phrase in base.get("phrases", [])]
    hook_data = base.get("hooks")
    source_language = base.get("sourceLanguage", "en")
    for registry in registries[1:]:
        if [phrase["id"] for phrase in registry.get("phrases", [])] != phrase_ids:
            fail("shards disagree about the phrase list")
        if registry.get("hooks") != hook_data:
            fail("shards disagree about hooks")
        if registry.get("sourceLanguage", "en") != source_language:
            fail("shards disagree about sourceLanguage")

    files: dict[str, dict[str, dict]] = {}
    voice_meta: dict[str, dict] = {}
    languages: set[str] = set()
    for registry in registries:
        for voice in registry.get("voices", []):
            voice_meta[voice["id"]] = voice
            languages.add(voice["language"])
        for voice_id, entries in registry.get("files", {}).items():
            bucket = files.setdefault(voice_id, {})
            for phrase_id, entry in entries.items():
                previous = bucket.get(phrase_id)
                if previous is not None and previous != entry:
                    fail(f"conflicting shard entries for {voice_id}/{phrase_id}")
                bucket[phrase_id] = entry

    config = load_json(ROOT / "config" / "voices.json")
    expected_voice_ids = [voice["id"] for voice in config["voices"]]
    missing_voices = [voice_id for voice_id in expected_voice_ids if voice_id not in voice_meta]
    if missing_voices:
        fail(f"shards are missing voices: {missing_voices}")

    voices = [voice_meta[voice_id] for voice_id in expected_voice_ids]
    if len(voices) * len(phrase_ids) != sum(len(entries) for entries in files.values()):
        fail("shard file count does not match voices x phrases")

    clear_directory(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for shard_dir in shard_dirs:
        audio_dir = shard_dir / "audio"
        if audio_dir.is_dir():
            shutil.copytree(audio_dir, out_dir / "audio", dirs_exist_ok=True)

    for voice_id, entries in files.items():
        for phrase_id, entry in entries.items():
            path = out_dir / entry["path"]
            if not path.is_file():
                fail(f"missing shard audio {entry['path']}")

    cache_files: dict[str, dict[str, dict]] = {}
    for shard_dir in shard_dirs:
        cache_path = shard_dir / ".clip-cache.json"
        if not cache_path.is_file():
            continue
        cache = load_json(cache_path).get("files", {})
        for voice_id, entries in cache.items():
            cache_files.setdefault(voice_id, {}).update(entries)
    for voice_id, entries in files.items():
        cache_files.setdefault(voice_id, {}).update(entries)

    clip_count = sum(len(entries) for entries in files.values())
    total_bytes = sum(
        entry["bytes"] for entries in files.values() for entry in entries.values()
    )
    total_ms = sum(
        entry["durationMs"] for entries in files.values() for entry in entries.values()
    )
    reused = sum(
        registry.get("build", {}).get("totals", {}).get("reused", 0)
        for registry in registries
    )
    generated = sum(
        registry.get("build", {}).get("totals", {}).get("generated", 0)
        for registry in registries
    )
    elapsed = round(
        sum(registry.get("build", {}).get("elapsedSeconds", 0) for registry in registries),
        3,
    )

    build = dict(base.get("build", {}))
    inputs = load_json(ROOT / "inputs.json")
    source_payload = {
        "inputs": inputs,
        "voices": config,
        "encoder": build.get("encoder"),
        "speed": build.get("pipeline", {}).get("speed"),
        "clipVersion": build.get("clipVersion"),
        "reproducible": build.get("pipeline", {}).get("reproducible", False),
        "selectedVoices": expected_voice_ids,
        "selectedPhrases": phrase_ids,
        "model": build.get("model", {}),
    }
    source_hash = hashlib.sha256(
        json.dumps(
            source_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    build["id"] = source_hash[:12]
    build["sourceHash"] = source_hash
    build["createdAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    build["elapsedSeconds"] = elapsed
    build["totals"] = {
        "clips": clip_count,
        "voices": len(voices),
        "phrases": len(phrase_ids),
        "bytes": total_bytes,
        "audioMs": total_ms,
        "audioSeconds": round(total_ms / 1000, 3),
        "reused": reused,
        "generated": generated,
    }

    registry = {
        "schemaVersion": base.get("schemaVersion", 1),
        "build": build,
        "sourceLanguage": source_language,
        "languages": sorted(languages),
        "defaultVoice": config.get("default") or voices[0]["id"],
        "voices": voices,
        "groups": base.get("groups", []),
        "phrases": base.get("phrases", []),
        "hooks": hook_data,
        "files": files,
    }

    (out_dir / "registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_site(out_dir, registry)
    (out_dir / ".clip-cache.json").write_text(
        json.dumps({"version": 1, "files": cache_files}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    print(
        f"merged {len(registries)} shards: "
        f"{len(voices)} voices, {len(phrase_ids)} phrases, {clip_count} clips, "
        f"{total_bytes / 1024 / 1024:.2f} MiB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
