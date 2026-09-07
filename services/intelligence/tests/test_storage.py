import pytest
from argus_ai.storage import DocumentResolutionError, resolved_document

def test_local_document_is_not_copied(tmp_path):
    source = tmp_path / "tender.txt"; source.write_text("text")
    with resolved_document(source) as path: assert path == source

def test_raw_s3_and_private_http_are_rejected():
    with pytest.raises(DocumentResolutionError):
        with resolved_document("s3://private-bucket/tender.pdf"): pass
    with pytest.raises(DocumentResolutionError):
        with resolved_document("https://127.0.0.1/document.pdf"): pass
