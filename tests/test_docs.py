"""The README and docs show rendered diagram images that are kept in sync with their sources."""
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAGRAMS = ROOT / "docs" / "diagrams"
DOCS = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]


def test_no_raw_mermaid_in_docs():
    for doc in DOCS:
        assert "```mermaid" not in doc.read_text("utf-8"), (
            f"{doc.name}: put the diagram in docs/diagrams/*.mmd and render it")


def test_diagram_images_exist():
    for doc in DOCS:
        for src in re.findall(r'<img src="([^"]+\.png)"', doc.read_text("utf-8")):
            assert (doc.parent / src).is_file(), f"{doc.name}: missing image {src}"


def test_rendered_diagrams_match_sources():
    manifest = json.loads((DIAGRAMS / "manifest.json").read_text("utf-8"))
    sources = sorted(DIAGRAMS.glob("*.mmd"))
    assert sources and set(manifest) == {p.stem for p in sources}
    for p in sources:
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        assert manifest[p.stem] == digest, (
            f"{p.name} changed since it was rendered: run python scripts/render_diagrams.py")
        assert p.with_suffix(".png").is_file()


def test_every_diagram_is_used():
    text = "".join(doc.read_text("utf-8") for doc in DOCS)
    for p in DIAGRAMS.glob("*.mmd"):
        assert f"diagrams/{p.stem}.png" in text, f"{p.stem} is not shown anywhere"
