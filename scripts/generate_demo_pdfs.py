"""Render the demo .txt corpus as text-layer PDFs the upload validator accepts.

Audit finding H-13: every demo document ships as .txt, but
DocumentValidationService allows only .pdf/.png/.jpg/.jpeg and enforces magic
bytes. So none of the shipped demo corpus could be uploaded through the product
it exists to demonstrate.

The validator is deliberately left strict. This converts the corpus instead.

Output is a real text layer (drawString, not a rasterised image), so pypdf's
extract_text() returns the content directly and the intelligence service never
falls back to OCR -- which would otherwise make demo extraction depend on
Tesseract accuracy.

    python scripts/generate_demo_pdfs.py

Regenerate after editing anything under data/demo/. Committed output lives in
data/demo/pdf/ alongside the .txt originals, which remain the source of truth.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - tooling dependency, not a runtime one
    print("ERROR: reportlab is required.  pip install reportlab", file=sys.stderr)
    raise SystemExit(1)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_ROOT = REPO_ROOT / "data" / "demo"
OUTPUT_ROOT = DEMO_ROOT / "pdf"

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_X = 18 * mm
MARGIN_TOP = 20 * mm
MARGIN_BOTTOM = 18 * mm
FONT_NAME = "Courier"
FONT_SIZE = 9
LINE_HEIGHT = 11.5
MAX_CHARS_PER_LINE = 96


def wrap(line: str, width: int = MAX_CHARS_PER_LINE) -> list[str]:
    """Hard-wrap a line, preserving leading indentation on continuations."""
    if len(line) <= width:
        return [line]
    indent = len(line) - len(line.lstrip())
    prefix = " " * indent
    words = line.split()
    wrapped: list[str] = []
    current = prefix
    for word in words:
        candidate = word if current.strip() == "" else f"{current} {word}"
        if len(candidate) > width and current.strip():
            wrapped.append(current)
            current = f"{prefix}{word}"
        else:
            current = candidate
    if current.strip():
        wrapped.append(current)
    return wrapped or [""]


def text_to_pdf(source: Path, destination: Path) -> int:
    """Write `source` as a text-layer PDF. Returns the page count."""
    raw_lines = source.read_text(encoding="utf-8").splitlines()

    lines: list[str] = []
    for raw in raw_lines:
        # Latin-1 is what the base-14 Courier font can encode; the demo corpus
        # uses a few typographic dashes that must be folded to ASCII.
        normalised = (
            raw.replace("—", "--")
            .replace("–", "-")
            .replace("‘", "'")
            .replace("’", "'")
            .replace("“", '"')
            .replace("”", '"')
            .replace("₹", "INR ")
        )
        normalised = normalised.encode("latin-1", "replace").decode("latin-1")
        lines.extend(wrap(normalised.rstrip()))

    destination.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(destination), pagesize=A4)
    pdf.setTitle(source.stem.replace("_", " ").title())
    pdf.setAuthor("ARGUS synthetic demo corpus")
    pdf.setSubject("SYNTHETIC DEMO DOCUMENT - NOT AN OFFICIAL RECORD")

    y = PAGE_HEIGHT - MARGIN_TOP
    pages = 1
    pdf.setFont(FONT_NAME, FONT_SIZE)

    for line in lines:
        if y < MARGIN_BOTTOM:
            pdf.showPage()
            pages += 1
            pdf.setFont(FONT_NAME, FONT_SIZE)
            y = PAGE_HEIGHT - MARGIN_TOP
        pdf.drawString(MARGIN_X, y, line)
        y -= LINE_HEIGHT

    pdf.save()
    return pages


def main() -> int:
    sources = sorted(p for p in DEMO_ROOT.rglob("*.txt") if OUTPUT_ROOT not in p.parents)
    if not sources:
        print(f"No .txt sources found under {DEMO_ROOT}", file=sys.stderr)
        return 1

    print(f"Rendering {len(sources)} demo document(s) to {OUTPUT_ROOT.relative_to(REPO_ROOT)}/")
    for source in sources:
        relative = source.relative_to(DEMO_ROOT).with_suffix(".pdf")
        destination = OUTPUT_ROOT / relative
        pages = text_to_pdf(source, destination)
        size_kb = destination.stat().st_size / 1024
        print(f"  {relative}  ({pages} page(s), {size_kb:.1f} KB)")

    print("Done. These PDFs satisfy the upload validator's extension, MIME and magic-byte checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
