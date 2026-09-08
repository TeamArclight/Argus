import pytest
from argus_ai.storage import DocumentResolutionError, resolved_document

def test_local_document_is_not_copied(tmp_path):
    source = tmp_path / "tender.txt"
    source.write_text("text")
    with resolved_document(source) as path:
        assert path == source

def test_file_uri_resolution(tmp_path):
    source = tmp_path / "tender.txt"
    source.write_text("text")
    file_uri = source.as_uri()
    with resolved_document(file_uri) as path:
        assert path.resolve() == source.resolve()

def test_raw_s3_and_private_http_are_rejected():
    with pytest.raises(DocumentResolutionError, match="S3 bucket is not allowlisted"):
        with resolved_document("s3://private-bucket/tender.pdf"):
            pass
    with pytest.raises(DocumentResolutionError, match="private download hosts are not allowed"):
        with resolved_document("https://127.0.0.1/document.pdf"):
            pass
    with pytest.raises(DocumentResolutionError, match="credential-bearing URLs are not allowed"):
        with resolved_document("https://user:pass@example.com/doc.pdf"):
            pass
    with pytest.raises(DocumentResolutionError, match="document_uri must be local/file://"):
        with resolved_document("http://insecure-http.com/doc.pdf"):
            pass

def test_allowed_storage_roots_containment(tmp_path, monkeypatch):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_root.mkdir()

    valid_doc = allowed_root / "doc.txt"
    valid_doc.write_text("valid content")

    escape_doc = outside_root / "secret.txt"
    escape_doc.write_text("secret content")

    monkeypatch.setenv("ARGUS_ALLOWED_STORAGE_ROOTS", str(allowed_root))

    with resolved_document(valid_doc) as path:
        assert path.read_text() == "valid content"

    with pytest.raises(DocumentResolutionError, match="escapes allowed storage roots"):
        with resolved_document(escape_doc):
            pass

def test_nonexistent_document_rejected(tmp_path):
    missing = tmp_path / "does_not_exist.txt"
    with pytest.raises(DocumentResolutionError, match="document not found"):
        with resolved_document(missing):
            pass
