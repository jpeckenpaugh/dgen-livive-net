#!/usr/bin/env python3
"""Serve the generated test site with the MIME types the browser needs."""

from __future__ import annotations

import argparse
import functools
import http.server
import os
from pathlib import Path


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".weba": "audio/webm",
        ".webm": "audio/webm",
        ".json": "application/json; charset=utf-8",
    }

    def end_headers(self) -> None:
        if self.path.endswith((".html", ".json")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=Path("dist"))
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        raise SystemExit(f"error: {directory} is not a directory")
    if not (directory / "index.html").is_file():
        raise SystemExit(f"error: {directory}/index.html not found; run make build first")

    handler = functools.partial(Handler, directory=str(directory))
    server = http.server.ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"serving {directory} at http://{args.bind}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
