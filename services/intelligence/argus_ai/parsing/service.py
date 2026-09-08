from __future__ import annotations

from pathlib import Path
from typing import Union


class DocumentParseError(ValueError): pass


def parse_document(file_path: Union[str, Path]) -> list[tuple[int, str]]:
    """Return one-indexed pages. PDF support is optional, never silently OCRs."""
    path = Path(file_path)
    if not path.is_file(): raise DocumentParseError(f"document not found: {path}")
    if path.suffix.lower() in {".txt", ".md", ".csv"}:
        return [(1, path.read_text(encoding="utf-8", errors="replace"))]
    if path.suffix.lower() == ".docx":
        try:
            from docx import Document
        except ImportError as exc:
            raise DocumentParseError("DOCX parsing requires python-docx") from exc
        document = Document(str(path))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs if paragraph.text.strip())
        for table in document.tables:
            text += "\n" + "\n".join(" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows)
        return [(1, text)]
    if path.suffix.lower() == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise DocumentParseError("XLSX parsing requires openpyxl") from exc
        workbook = load_workbook(path, read_only=True, data_only=True)
        pages = []
        for index, sheet in enumerate(workbook.worksheets, 1):
            rows = [" | ".join("" if value is None else str(value) for value in row) for row in sheet.iter_rows(values_only=True)]
            pages.append((index, "\n".join(row for row in rows if row.strip())))
        return pages
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        try:
            from PIL import Image
            import pytesseract
            text = pytesseract.image_to_string(Image.open(path))
        except ImportError as exc:
            raise DocumentParseError("Image parsing requires Pillow and pytesseract") from exc
        except Exception as exc:
            raise DocumentParseError(f"Image OCR failed: {exc}") from exc
        if not text.strip(): raise DocumentParseError("OCR produced no readable text")
        return [(1, text)]
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
            pages = [(i + 1, page.extract_text() or "") for i, page in enumerate(PdfReader(str(path)).pages)]
            if any(text.strip() for _, text in pages): return pages
            return _ocr_pdf(path)
        except ImportError as exc:
            raise DocumentParseError("PDF parsing requires optional dependency pypdf") from exc
    raise DocumentParseError(f"unsupported document type: {path.suffix}")


def _ocr_pdf(path: Path) -> list[tuple[int, str]]:
    """Opt-in OCR fallback; native PDF text is always preferred."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
        pages = [(index + 1, pytesseract.image_to_string(image)) for index, image in enumerate(convert_from_path(str(path)))]
    except ImportError as exc:
        raise DocumentParseError("scanned PDF needs OCR dependencies: pdf2image and pytesseract") from exc
    except Exception as exc:
        raise DocumentParseError(f"scanned PDF OCR failed: {exc}") from exc
    if not any(text.strip() for _, text in pages): raise DocumentParseError("OCR produced no readable text")
    return pages
