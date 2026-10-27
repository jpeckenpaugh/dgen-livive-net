#!/usr/bin/env python3
"""Build the voice pack in parallel Docker shards, then merge the results."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def normalize_ownership(path: Path) -> None:
    if os.geteuid() == 0 or not sys.platform.startswith("linux"):
        return
    subprocess.run(
        ["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(path)],
        check=False,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--threads",
        type=int,
        default=0,
        help="Torch threads per worker (0 = CPU count / workers)",
    )
    parser.add_argument(
        "--image", default="dgen-livive-net-builder:local"
    )
    parser.add_argument(
        "--prefetch",
        action="store_true",
        help="Prefetch the model and all selected voices before starting shards",
    )
    parser.add_argument(
        "--reproducible",
        action="store_true",
        help="Pass --reproducible to each shard",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Pass --force to each shard",
    )
    parser.add_argument(
        "--keep-shards",
        action="store_true",
        help="Keep the temporary shard directories for inspection",
    )
    args = parser.parse_args()

    out_dir = args.out.expanduser().resolve()
    config = json.loads((ROOT / "config" / "voices.json").read_text(encoding="utf-8"))
    all_voices = [voice["id"] for voice in config["voices"]]
    worker_count = max(1, min(args.workers, len(all_voices)))
    shards = [all_voices[index::worker_count] for index in range(worker_count)]
    shards = [shard for shard in shards if shard]
    threads = args.threads or max(1, (os.cpu_count() or 1) // len(shards))

    cache_dir = ROOT / ".cache" / "huggingface"
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(prefix="dgen-parallel-"))
    shard_dirs: list[Path] = []
    started = time.monotonic()

    try:
        if args.prefetch:
            print(f"prefetching all {len(all_voices)} voices", flush=True)
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{cache_dir}:/cache/huggingface",
                    args.image,
                    "--prefetch",
                ],
                check=True,
            )
            normalize_ownership(cache_dir)

        print(
            f"building {len(all_voices)} voices in {len(shards)} shards "
            f"({threads} thread(s) each)",
            flush=True,
        )

        processes: list[tuple[int, subprocess.Popen, Path, Path]] = []
        for index, voices in enumerate(shards):
            shard_dir = temp_root / f"shard-{index}"
            shard_dir.mkdir(parents=True)
            shutil.copytree(out_dir, shard_dir, dirs_exist_ok=True, symlinks=True)
            shard_dirs.append(shard_dir)
            log_path = temp_root / f"shard-{index}.log"
            log_file = log_path.open("w", encoding="utf-8")
            command = [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{shard_dir}:/out",
                "-v",
                f"{cache_dir}:/cache/huggingface",
                args.image,
                "--out",
                "/out",
                "--voices",
                ",".join(voices),
                "--threads",
                str(threads),
            ]
            if args.reproducible:
                command.append("--reproducible")
            if args.force:
                command.append("--force")
            print(f"  shard {index}: {', '.join(voices)}", flush=True)
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            processes.append((index, process, log_path, shard_dir))

        failed = False
        for index, process, log_path, _ in processes:
            returncode = process.wait()
            if returncode != 0:
                failed = True
                print(
                    f"shard {index} failed with exit code {returncode}",
                    file=sys.stderr,
                )
                print(log_path.read_text(encoding="utf-8")[-6000:], file=sys.stderr)
            else:
                print(f"  shard {index}: done", flush=True)

        if failed:
            fail("one or more shards failed")

        normalize_ownership(temp_root)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "merge.py"),
                "--shards",
                str(temp_root),
                "--out",
                str(out_dir),
            ],
            check=True,
        )
        normalize_ownership(out_dir)
        print(
            f"parallel build complete in {time.monotonic() - started:.1f}s",
            flush=True,
        )
    finally:
        if not args.keep_shards:
            shutil.rmtree(temp_root, ignore_errors=True)
        elif shard_dirs:
            print(f"shards kept at {temp_root}", flush=True)


if __name__ == "__main__":
    main()
