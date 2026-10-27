#!/usr/bin/env python3
"""Validate a generated dist/ directory against its registry."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=ROOT / "dist")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat unreferenced audio files as errors",
    )
    args = parser.parse_args()

    dist = args.directory.expanduser().resolve()
    registry_path = dist / "registry.json"
    if not registry_path.is_file():
        print(f"error: {registry_path} not found", file=sys.stderr)
        return 1

    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"error: invalid {registry_path}: {exc}", file=sys.stderr)
        return 1

    errors: list[str] = []
    phrase_ids = {phrase["id"] for phrase in registry.get("phrases", [])}
    voices = registry.get("voices", [])
    phrases = registry.get("phrases", [])
    files = registry.get("files", {})
    referenced: set[Path] = set()

    for voice in voices:
        voice_id = voice["id"]
        if voice_id not in files:
            fail(errors, f"no files for voice {voice_id}")
            continue
        for phrase in phrases:
            phrase_id = phrase["id"]
            entry = files[voice_id].get(phrase_id)
            if entry is None:
                fail(errors, f"missing registry entry {voice_id}/{phrase_id}")
                continue

            path = dist / entry["path"]
            referenced.add(path.resolve())
            if not path.is_file():
                fail(errors, f"missing file {entry['path']}")
                continue

            data = path.read_bytes()
            if len(data) != entry["bytes"]:
                fail(
                    errors,
                    f"size mismatch {entry['path']}: "
                    f"{len(data)} != {entry['bytes']}",
                )
            digest = hashlib.sha256(data).hexdigest()
            if digest != entry["sha256"]:
                fail(errors, f"hash mismatch {entry['path']}")

    for hook, ids in registry.get("hooks", {}).items():
        missing = sorted(set(ids) - phrase_ids)
        if missing:
            fail(errors, f"hook {hook} references unknown phrases: {missing}")

    orphans = [
        path.relative_to(dist)
        for path in sorted((dist / "audio").rglob("*.weba"))
        if path.resolve() not in referenced
    ]
    if args.strict:
        for path in orphans:
            fail(errors, f"orphan audio file {path}")

    totals = registry.get("build", {}).get("totals", {})
    expected = len(voices) * len(phrases)
    if totals.get("clips") != expected:
        fail(errors, f"registry totals.clips {totals.get('clips')} != {expected}")

    print(
        f"voices={len(voices)} phrases={len(phrases)} "
        f"clips={totals.get('clips')} bytes={totals.get('bytes')} "
        f"reused={totals.get('reused', 0)} generated={totals.get('generated', 0)}"
    )
    if orphans and not args.strict:
        print(
            f"warning: {len(orphans)} unreferenced audio file(s) left in {dist / 'audio'} "
            "(run `make clean` for a pristine output)"
        )
    if errors:
        print(f"\n{len(errors)} problem(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
