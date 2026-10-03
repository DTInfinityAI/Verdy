#!/usr/bin/env python3
"""Render the Mermaid diagrams in docs/diagrams/*.mmd to PNG.

The README and the docs show rendered images, so they read the same on GitHub, PyPI and
any Markdown viewer. Edit the .mmd source, then run:

    python scripts/render_diagrams.py            # render changed diagrams
    python scripts/render_diagrams.py --all      # render every diagram
    python scripts/render_diagrams.py --check    # exit 1 if a PNG is stale (no rendering)

Rendering needs Node.js and mermaid-cli (``npx -y @mermaid-js/mermaid-cli``, or ``mmdc`` on
PATH). Set ``PUPPETEER_EXECUTABLE_PATH`` to use an installed Chrome/Chromium.
``manifest.json`` records the SHA-256 of each source a PNG was rendered from;
``tests/test_docs.py`` fails when a source changed without re-rendering.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
MANIFEST = DIAGRAMS / "manifest.json"
SCALE = "2"  # 2x pixels, shown at half size for sharp images on high-DPI screens
# Natural size: every diagram gets the same text size instead of being shrunk to fit.
MERMAID_CONFIG = {key: {"useMaxWidth": False} for key in ("flowchart", "er", "class")}


def source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest() -> dict[str, str]:
    return json.loads(MANIFEST.read_text("utf-8")) if MANIFEST.is_file() else {}


def stale(manifest: dict[str, str]) -> list[Path]:
    return [p for p in sorted(DIAGRAMS.glob("*.mmd"))
            if manifest.get(p.stem) != source_sha256(p) or not p.with_suffix(".png").is_file()]


def mmdc_command() -> list[str]:
    if shutil.which("mmdc"):
        return ["mmdc"]
    if shutil.which("npx"):
        return ["npx", "-y", "@mermaid-js/mermaid-cli"]
    sys.exit("mermaid-cli not found: install Node.js, then npm i -g @mermaid-js/mermaid-cli")


def render(paths: list[Path]) -> None:
    cmd = mmdc_command()
    with tempfile.TemporaryDirectory() as tmp:
        puppeteer = Path(tmp) / "puppeteer.json"
        config: dict = {"args": ["--no-sandbox"]}
        if os.environ.get("PUPPETEER_EXECUTABLE_PATH"):
            config["executablePath"] = os.environ["PUPPETEER_EXECUTABLE_PATH"]
        puppeteer.write_text(json.dumps(config))
        mermaid = Path(tmp) / "mermaid.json"
        mermaid.write_text(json.dumps(MERMAID_CONFIG))
        for p in paths:
            out = p.with_suffix(".png")
            subprocess.run([*cmd, "-p", str(puppeteer), "-c", str(mermaid),
                            "-i", str(p), "-o", str(out),
                            "-b", "white", "-s", SCALE], check=True,
                           stdout=subprocess.DEVNULL)
            print(f"rendered {out.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="render every diagram")
    parser.add_argument("--check", action="store_true", help="only report stale diagrams")
    args = parser.parse_args()
    manifest = load_manifest()
    todo = sorted(DIAGRAMS.glob("*.mmd")) if args.all else stale(manifest)
    if args.check:
        for p in todo:
            print(f"stale: {p.relative_to(ROOT)}")
        return 1 if todo else 0
    if todo:
        render(todo)
    manifest = {p.stem: source_sha256(p) for p in sorted(DIAGRAMS.glob("*.mmd"))}
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    print(f"{len(todo)} rendered, {len(manifest)} diagrams up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
