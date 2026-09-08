"""Public entry points for ARGUS intelligence capabilities."""

from pathlib import Path
from typing import Any, Optional

from .extraction.service import classify_document as _classify_document
from .extraction.service import extract_document, extract_tender as _extract_tender
from .parsing.service import parse_document
from .rag.service import InMemoryRAG
from .risk.service import detect_risk


def extract_tender(file_path: str):
    return _extract_tender(file_path)


def classify_document(file_path: str):
    return _classify_document(file_path)


def extract_bidder_document(file_path: str, *, document_id: Optional[str] = None, bidder_id: Optional[str] = None):
    path = Path(file_path)
    resolved_id = document_id or path.stem
    return extract_document(path, document_id=resolved_id, bidder_id=bidder_id or resolved_id)


def retrieve_policy(query: str, *, rag: Optional[Any] = None):
    """Retrieve policy evidence from an injected or default in-memory index."""
    return (rag or InMemoryRAG()).retrieve(query)


__all__ = [
    "classify_document",
    "extract_bidder_document",
    "extract_document",
    "extract_tender",
    "detect_risk",
    "parse_document",
    "retrieve_policy",
]
