import io
import os
import shutil
import tempfile
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import Base, engine, get_db
from app.main import app
from app.models.domain import AuditEvent, Document, Tender
from app.schemas.canonical import DocumentType, UserRole
from app.services.document_validation_service import DocumentValidationService
from app.storage.local import LocalStorageProvider
from tests.auth_helpers import get_auth_headers


@pytest.fixture(autouse=True)
def setup_database_and_storage(monkeypatch, tmp_path):
    # Use temporary directory for storage tests
    storage_dir = str(tmp_path / "uploads")
    monkeypatch.setattr(settings, "ARGUS_STORAGE_LOCAL_PATH", storage_dir)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


# ---------------------------------------------------------------------------
# 1. STORAGE PROVIDER TESTS
# ---------------------------------------------------------------------------

def test_local_storage_provider_operations(tmp_path):
    provider = LocalStorageProvider(base_path=str(tmp_path / "storage"))
    content = b"%PDF-1.4 sample pdf content for unit test"
    uri = provider.store_file(content, target_key="tenders/t1/test_sample.pdf")

    assert uri == "tenders/t1/test_sample.pdf"
    assert provider.file_exists(uri) is True

    read_bytes = provider.read_file(uri)
    assert read_bytes == content

    metadata = provider.get_metadata(uri)
    assert metadata["size_bytes"] == len(content)

    # Path traversal containment assertion
    with pytest.raises(ValueError, match="Path traversal"):
        provider.store_file(content, target_key="../../etc/passwd.pdf")

    # Clean up file
    deleted = provider.delete_file(uri)
    assert deleted is True
    assert provider.file_exists(uri) is False


# ---------------------------------------------------------------------------
# 2. DOCUMENT VALIDATION SERVICE TESTS
# ---------------------------------------------------------------------------

def test_document_validation_allowed_extensions_and_signatures():
    # Valid PDF
    pdf_bytes = b"%PDF-1.7 header content"
    val = DocumentValidationService.validate_file("sample.pdf", pdf_bytes)
    assert val.sanitized_filename == "sample.pdf"
    assert len(val.sha256_hex) == 64
    assert val.content_type == "application/pdf"

    # Valid PNG
    png_bytes = b"\x89PNG\r\n\x1a\nheader content"
    val = DocumentValidationService.validate_file("sample.png", png_bytes)
    assert val.sanitized_filename == "sample.png"
    assert val.content_type == "image/png"

    # Valid JPEG
    jpeg_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    val = DocumentValidationService.validate_file("photo.jpg", jpeg_bytes)
    assert val.sanitized_filename == "photo.jpg"

    # Valid DOCX (ZIP signature)
    import zipfile
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
    docx_bytes = bio.getvalue()
    val = DocumentValidationService.validate_file("contract.docx", docx_bytes)
    assert val.sanitized_filename == "contract.docx"


def test_document_validation_rejections():
    # 0-byte empty file
    with pytest.raises(HTTPException) as exc_info:
        DocumentValidationService.validate_file("empty.pdf", b"")
    assert exc_info.value.status_code == 400

    # Invalid extension
    with pytest.raises(HTTPException) as exc_info:
        DocumentValidationService.validate_file("script.py", b"%PDF-1.4 text")
    assert exc_info.value.status_code == 415

    # Executable extension
    with pytest.raises(HTTPException) as exc_info:
        DocumentValidationService.validate_file("run.exe", b"%PDF-1.4 text")
    assert exc_info.value.status_code == 415

    # Double extension executable
    with pytest.raises(HTTPException) as exc_info:
        DocumentValidationService.validate_file("invoice.pdf.exe", b"%PDF-1.4 text")
    assert exc_info.value.status_code == 415

    # Magic signature mismatch (.pdf file containing non-pdf content)
    with pytest.raises(HTTPException) as exc_info:
        DocumentValidationService.validate_file("fake.pdf", b"PLAIN TEXT CONTENT NOT PDF")
    assert exc_info.value.status_code == 415


# ---------------------------------------------------------------------------
# 3. TENDER DOCUMENT UPLOAD & RETRIEVAL ENDPOINT TESTS
# ---------------------------------------------------------------------------

def test_tender_document_upload_and_retrieval():
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        # Create tender
        t_res = client.post(
            "/api/v1/tenders",
            json={"tender_number": "GEM/2026/DOC/001", "title": "Tender Document Test"},
            headers=headers,
        )
        assert t_res.status_code == 201
        tender_id = t_res.json()["id"]

        pdf_content = b"%PDF-1.4 GeM Tender Document Content"
        files = {"file": ("tender_notice.pdf", pdf_content, "application/pdf")}
        data = {"document_type": DocumentType.TENDER.value}

        # Upload document
        up_res = client.post(
            f"/api/v1/tenders/{tender_id}/documents",
            headers=headers,
            files=files,
            data=data,
        )
        assert up_res.status_code == 201, up_res.text
        doc_json = up_res.json()
        assert doc_json["tender_id"] == tender_id
        assert doc_json["filename"] == "tender_notice.pdf"
        assert doc_json["document_type"] == DocumentType.TENDER.value
        assert doc_json["size_bytes"] == len(pdf_content)
        assert doc_json["sha256"] is not None
        doc_id = doc_json["id"]

        # Verify tender raw_document_uri updated
        get_t_res = client.get(f"/api/v1/tenders/{tender_id}", headers=headers)
        assert get_t_res.json()["raw_document_uri"] == doc_json["storage_uri"]

        # List tender documents
        list_res = client.get(f"/api/v1/tenders/{tender_id}/documents", headers=headers)
        assert list_res.status_code == 200
        docs = list_res.json()
        assert len(docs) == 1
        assert docs[0]["id"] == doc_id

        # Get document metadata by ID
        get_doc_res = client.get(f"/api/v1/documents/{doc_id}", headers=headers)
        assert get_doc_res.status_code == 200
        assert get_doc_res.json()["id"] == doc_id

        # Download document content
        content_res = client.get(f"/api/v1/documents/{doc_id}/content", headers=headers)
        assert content_res.status_code == 200
        assert content_res.content == pdf_content
        assert "inline; filename=\"tender_notice.pdf\"" in content_res.headers["content-disposition"]


# ---------------------------------------------------------------------------
# 4. BIDDER DOCUMENT UPLOAD & RETRIEVAL ENDPOINT TESTS
# ---------------------------------------------------------------------------

def test_bidder_document_upload_and_retrieval():
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        # Create tender & bidder
        t_res = client.post(
            "/api/v1/tenders",
            json={"tender_number": "GEM/2026/DOC/002", "title": "Bidder Document Test Tender"},
            headers=headers,
        )
        tender_id = t_res.json()["id"]

        b_res = client.post(
            f"/api/v1/tenders/{tender_id}/bidders",
            json={"bidder_name": "Doc Test Bidder", "gstin": "27ABCDE1234F1Z5"},
            headers=headers,
        )
        bidder_id = b_res.json()["id"]

        png_content = b"\x89PNG\r\n\x1a\n GST Certificate Image Bytes"
        files = {"file": ("gst_cert.png", png_content, "image/png")}
        data = {"document_type": DocumentType.GST_CERT.value}

        up_res = client.post(
            f"/api/v1/bidders/{bidder_id}/documents",
            headers=headers,
            files=files,
            data=data,
        )
        assert up_res.status_code == 201, up_res.text
        doc_json = up_res.json()
        assert doc_json["bidder_id"] == bidder_id
        assert doc_json["tender_id"] is None
        assert doc_json["document_type"] == DocumentType.GST_CERT.value

        # List bidder documents
        list_res = client.get(f"/api/v1/bidders/{bidder_id}/documents", headers=headers)
        assert list_res.status_code == 200
        assert len(list_res.json()) == 1


# ---------------------------------------------------------------------------
# 5. DUPLICATE DOCUMENT UPLOAD CONFLICT (409)
# ---------------------------------------------------------------------------

def test_duplicate_document_upload_conflict():
    headers = get_auth_headers(UserRole.ADMIN)
    with TestClient(app) as client:
        t_res = client.post(
            "/api/v1/tenders",
            json={"tender_number": "GEM/2026/DOC/003", "title": "Dup Test Tender"},
            headers=headers,
        )
        tender_id = t_res.json()["id"]

        pdf_content = b"%PDF-1.4 Duplicate Document Content"
        files1 = {"file": ("file1.pdf", pdf_content, "application/pdf")}
        data = {"document_type": DocumentType.TENDER.value}

        # First upload succeeds
        res1 = client.post(f"/api/v1/tenders/{tender_id}/documents", headers=headers, files=files1, data=data)
        assert res1.status_code == 201

        # Second upload of identical content fails with 409 Conflict
        files2 = {"file": ("file2_same_hash.pdf", pdf_content, "application/pdf")}
        res2 = client.post(f"/api/v1/tenders/{tender_id}/documents", headers=headers, files=files2, data=data)
        assert res2.status_code == 409
        err_json = res2.json()
        assert err_json["error"]["code"] == "DOCUMENT_DUPLICATE"
        assert "identical content" in err_json["error"]["message"]


# ---------------------------------------------------------------------------
# 6. DOCUMENT SIZE LIMIT EXCEEDED (413)
# ---------------------------------------------------------------------------

def test_document_size_limit_exceeded(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_MAX_UPLOAD_MB", 1)  # 1 MB limit
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        t_res = client.post(
            "/api/v1/tenders",
            json={"tender_number": "GEM/2026/DOC/004", "title": "Size Limit Test"},
            headers=headers,
        )
        tender_id = t_res.json()["id"]

        # 1.5 MB file content with valid PDF header
        oversized_content = b"%PDF-1.4 " + (b"X" * (1500 * 1024))
        files = {"file": ("oversized.pdf", oversized_content, "application/pdf")}
        data = {"document_type": DocumentType.TENDER.value}

        res = client.post(f"/api/v1/tenders/{tender_id}/documents", headers=headers, files=files, data=data)
        assert res.status_code == 413
        err_json = res.json()
        assert err_json["error"]["code"] == "DOCUMENT_SIZE_EXCEEDED"
        assert "exceeds maximum limit" in err_json["error"]["message"]


# ---------------------------------------------------------------------------
# 7. DOCUMENT DELETION & RBACS
# ---------------------------------------------------------------------------

def test_document_deletion_permissions():
    officer_headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    reviewer_headers = get_auth_headers(UserRole.REVIEWER)
    admin_headers = get_auth_headers(UserRole.ADMIN)

    with TestClient(app) as client:
        # Create tender and upload doc as Officer
        t_res = client.post(
            "/api/v1/tenders",
            json={"tender_number": "GEM/2026/DOC/005", "title": "Delete Test Tender"},
            headers=officer_headers,
        )
        tender_id = t_res.json()["id"]

        pdf_content = b"%PDF-1.4 Document to be deleted"
        up_res = client.post(
            f"/api/v1/tenders/{tender_id}/documents",
            headers=officer_headers,
            files={"file": ("to_delete.pdf", pdf_content, "application/pdf")},
            data={"document_type": DocumentType.TENDER.value},
        )
        assert up_res.status_code == 201, up_res.text
        doc_id = up_res.json()["id"]

        # Reviewer cannot delete -> 403
        del_rev = client.delete(f"/api/v1/documents/{doc_id}", headers=reviewer_headers)
        assert del_rev.status_code == 403

        # Admin can delete -> 200
        del_adm = client.delete(f"/api/v1/documents/{doc_id}", headers=admin_headers)
        assert del_adm.status_code == 200
        assert del_adm.json()["status"] == "deleted"

        # Subsequent GET yields 404
        get_res = client.get(f"/api/v1/documents/{doc_id}", headers=officer_headers)
        assert get_res.status_code == 404


# ---------------------------------------------------------------------------
# 8. UPLOAD RBAC & AUTHENTICATION PERMISSIONS
# ---------------------------------------------------------------------------

def test_upload_rbac_permissions():
    auditor_headers = get_auth_headers(UserRole.AUDITOR)
    reviewer_headers = get_auth_headers(UserRole.REVIEWER)
    officer_headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)

    with TestClient(app) as client:
        t_res = client.post(
            "/api/v1/tenders",
            json={"tender_number": "GEM/2026/DOC/006", "title": "RBAC Upload Test"},
            headers=officer_headers,
        )
        tender_id = t_res.json()["id"]

        pdf_content = b"%PDF-1.4 RBAC test content"
        files = {"file": ("rbac.pdf", pdf_content, "application/pdf")}
        data = {"document_type": DocumentType.TENDER.value}

        # Auditor upload fails -> 403
        res_aud = client.post(f"/api/v1/tenders/{tender_id}/documents", headers=auditor_headers, files=files, data=data)
        assert res_aud.status_code == 403

        # Reviewer upload fails -> 403
        res_rev = client.post(f"/api/v1/tenders/{tender_id}/documents", headers=reviewer_headers, files=files, data=data)
        assert res_rev.status_code == 403

        # Unauthenticated upload fails -> 401
        res_unauth = client.post(f"/api/v1/tenders/{tender_id}/documents", files=files, data=data)
        assert res_unauth.status_code == 401
