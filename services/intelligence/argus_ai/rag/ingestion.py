"""Corpus ingestion boundary for policy and tender evidence.

Input schema: local file path plus document metadata.
Output schema: list[EvidenceChunk].  Storage download/auth remains a backend-owned
adapter concern; this module deliberately never fetches arbitrary URLs.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Protocol, Union

from ..contracts import EvidenceChunk
from ..parsing.service import parse_document


class EvidenceIndexer(Protocol):
    def index(self, document_id: str, title: str, text: str, *, page: Optional[int] = None, **metadata: Any) -> list[EvidenceChunk]: ...


def ingest_document(
    file_path: Union[str, Path], *, document_id: str, title: str,
    document_type: str, indexer: EvidenceIndexer, source_uri: Optional[str] = None,
    version: Optional[str] = None, effective_from: Optional[datetime] = None,
    effective_to: Optional[datetime] = None, security_level: str = "INTERNAL",
    tender_id: Optional[str] = None, clause: Optional[str] = None,
) -> list[EvidenceChunk]:
    """Index each nonblank parsed page with immutable provenance metadata."""
    metadata: dict[str, Any] = {"document_type": document_type, "security_level": security_level}
    if source_uri: metadata["source_uri"] = source_uri
    if tender_id: metadata["tender_id"] = tender_id
    if clause: metadata["clause"] = clause
    if version: metadata["version"] = version
    if effective_from: metadata["effective_from"] = effective_from
    if effective_to: metadata["effective_to"] = effective_to
    indexed: list[EvidenceChunk] = []
    for page, text in parse_document(file_path):
        if text.strip():
            chunks = indexer.index(document_id, title, text, page=page, **metadata)
            for chunk in chunks:
                # Preserve source URI without trusting it as document content.
                indexed.append(chunk.model_copy(update={"source_uri": source_uri, "version": version, "effective_from": effective_from, "effective_to": effective_to, "security_level": security_level}))
    return indexed
