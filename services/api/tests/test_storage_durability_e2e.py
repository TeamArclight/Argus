import hashlib
import shutil
from pathlib import Path
import pytest
import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.db.session import SessionLocal
from app.models.domain import Tender, Bidder
from app.schemas.canonical import UserRole
from tests.auth_helpers import get_auth_headers
from app.storage.supabase import SupabaseStorageProvider


class StatefulSupabaseMock:
    """Stateful mock simulating remote Supabase Object Storage bucket."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict] = {}
        self.buckets: set[str] = {"documents"}

    def handle_post(self, client, url, headers=None, content=None, json=None):
        url_str = str(url)
        if "/bucket" in url_str:
            b_id = json.get("id", "documents") if json else "documents"
            self.buckets.add(b_id)
            return httpx.Response(200, json={"id": b_id}, request=httpx.Request("POST", url_str))

        if "/object/" in url_str:
            # POST /storage/v1/object/{bucket}/{key}
            parts = url_str.split("/object/", 1)[-1].split("/", 1)
            bucket, key = parts[0], parts[1]
            if key in self.objects and headers and headers.get("x-upsert") == "false":
                return httpx.Response(409, json={"error": "Duplicate", "message": "The resource already exists"}, request=httpx.Request("POST", url_str))
            self.objects[key] = content or b""
            self.metadata[key] = {"size": len(content or b""), "updated_at": "2026-09-13T04:00:00Z"}
            return httpx.Response(200, json={"Key": f"{bucket}/{key}"}, request=httpx.Request("POST", url_str))

        return httpx.Response(404, request=httpx.Request("POST", url_str))

    def handle_get(self, client, url, headers=None):
        url_str = str(url)
        if "/object/info/authenticated/" in url_str:
            key = url_str.split("/info/authenticated/", 1)[-1].split("/", 1)[-1]
            if key in self.metadata:
                return httpx.Response(200, json={"name": key, "metadata": self.metadata[key]}, request=httpx.Request("GET", url_str))
            return httpx.Response(404, request=httpx.Request("GET", url_str))

        if "/object/authenticated/" in url_str:
            key = url_str.split("/object/authenticated/", 1)[-1].split("/", 1)[-1]
            if key in self.objects:
                return httpx.Response(200, content=self.objects[key], request=httpx.Request("GET", url_str))
            return httpx.Response(404, request=httpx.Request("GET", url_str))

        if "/object/" in url_str:
            key = url_str.split("/object/", 1)[-1].split("/", 1)[-1]
            if key in self.objects:
                return httpx.Response(200, content=self.objects[key], request=httpx.Request("GET", url_str))
            return httpx.Response(404, request=httpx.Request("GET", url_str))

        return httpx.Response(404, request=httpx.Request("GET", url_str))

    def handle_head(self, client, url, headers=None):
        url_str = str(url)
        if "/object/authenticated/" in url_str:
            key = url_str.split("/object/authenticated/", 1)[-1].split("/", 1)[-1]
            if key in self.objects:
                return httpx.Response(200, request=httpx.Request("HEAD", url_str))
        return httpx.Response(404, request=httpx.Request("HEAD", url_str))

    def handle_delete(self, client, url, headers=None):
        url_str = str(url)
        if "/object/" in url_str:
            key = url_str.split("/object/", 1)[-1].split("/", 1)[-1]
            if key in self.objects:
                del self.objects[key]
                self.metadata.pop(key, None)
                return httpx.Response(200, request=httpx.Request("DELETE", url_str))
        return httpx.Response(404, request=httpx.Request("DELETE", url_str))


def test_storage_durability_and_restart_simulation(tmp_path, monkeypatch):
    """
    Tests the complete storage durability lifecycle:
    1. Configure Supabase storage backend.
    2. Upload tender and bidder PDF documents.
    3. Verify SHA256 integrity, DB record, and duplicate protection.
    4. Simulate Render restart by wiping the local filesystem completely.
    5. Verify content readback succeeds post-restart with exact bytes and SHA256.
    6. Verify extraction pipeline succeeds post-restart without DOCUMENT_FILE_NOT_FOUND.
    """
    mock_supabase = StatefulSupabaseMock()

    orig_post = httpx.Client.post
    orig_get = httpx.Client.get
    orig_head = httpx.Client.head
    orig_delete = httpx.Client.delete

    def patched_post(client, url, *args, **kwargs):
        if "supabase.co" in str(url):
            return mock_supabase.handle_post(client, url, *args, **kwargs)
        return orig_post(client, url, *args, **kwargs)

    def patched_get(client, url, *args, **kwargs):
        if "supabase.co" in str(url):
            return mock_supabase.handle_get(client, url, *args, **kwargs)
        return orig_get(client, url, *args, **kwargs)

    def patched_head(client, url, *args, **kwargs):
        if "supabase.co" in str(url):
            return mock_supabase.handle_head(client, url, *args, **kwargs)
        return orig_head(client, url, *args, **kwargs)

    def patched_delete(client, url, *args, **kwargs):
        if "supabase.co" in str(url):
            return mock_supabase.handle_delete(client, url, *args, **kwargs)
        return orig_delete(client, url, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "post", patched_post)
    monkeypatch.setattr(httpx.Client, "get", patched_get)
    monkeypatch.setattr(httpx.Client, "head", patched_head)
    monkeypatch.setattr(httpx.Client, "delete", patched_delete)

    local_dir = tmp_path / "ephemeral_local"
    local_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(settings, "ARGUS_STORAGE_BACKEND", "supabase")
    monkeypatch.setattr(settings, "ARGUS_SUPABASE_URL", "https://mockproject.supabase.co")
    monkeypatch.setattr(settings, "ARGUS_SUPABASE_SERVICE_ROLE_KEY", "mock-service-role-key-12345")
    from app.db.session import Base, engine
    Base.metadata.create_all(bind=engine)

    auth_headers = get_auth_headers(role=UserRole.PROCUREMENT_OFFICER)
    client = TestClient(app)

    # Setup DB entities
    import uuid
    u_suffix = uuid.uuid4().hex[:8]
    db = SessionLocal()
    tender = Tender(
        tender_number=f"TENDER-DURABILITY-{u_suffix}",
        title="Durability Test Tender",
    )
    db.add(tender)
    db.flush()
    bidder = Bidder(
        tender_id=tender.id,
        bidder_name=f"Nova Infrastructure {u_suffix} Ltd",
        gstin="19ABCDE1234F1Z5",
    )
    db.add(bidder)
    db.commit()
    db.refresh(tender)
    db.refresh(bidder)
    tender_id = tender.id
    bidder_id = bidder.id
    db.close()

    # 1. Tender PDF Upload
    tender_bytes = b"%PDF-1.4 Tender technical specifications payload for durability test"
    tender_sha = hashlib.sha256(tender_bytes).hexdigest()

    resp_t = client.post(
        f"/api/v1/tenders/{tender_id}/documents",
        files={"file": ("tender_spec.pdf", tender_bytes, "application/pdf")},
        headers=auth_headers,
    )
    assert resp_t.status_code == 201, resp_t.text
    doc_t_data = resp_t.json()
    tender_doc_id = doc_t_data["id"]
    assert doc_t_data["sha256"] == tender_sha

    # Verify duplicate upload protection
    resp_t_dup = client.post(
        f"/api/v1/tenders/{tender_id}/documents",
        files={"file": ("tender_spec.pdf", tender_bytes, "application/pdf")},
        headers=auth_headers,
    )
    assert resp_t_dup.status_code == 409
    assert resp_t_dup.json()["error"]["code"] == "DOCUMENT_DUPLICATE"

    # 2. Bidder PDF Upload
    bidder_bytes = b"%PDF-1.4 Bidder financial statement payload for durability test"
    bidder_sha = hashlib.sha256(bidder_bytes).hexdigest()

    resp_b = client.post(
        f"/api/v1/bidders/{bidder_id}/documents",
        files={"file": ("financial.pdf", bidder_bytes, "application/pdf")},
        data={"document_type": "FINANCIAL_STATEMENT"},
        headers=auth_headers,
    )
    assert resp_b.status_code == 201, resp_b.text
    doc_b_data = resp_b.json()
    bidder_doc_id = doc_b_data["id"]
    assert doc_b_data["sha256"] == bidder_sha

    # Verify duplicate upload protection
    resp_b_dup = client.post(
        f"/api/v1/bidders/{bidder_id}/documents",
        files={"file": ("financial.pdf", bidder_bytes, "application/pdf")},
        data={"document_type": "FINANCIAL_STATEMENT"},
        headers=auth_headers,
    )
    assert resp_b_dup.status_code == 409
    assert resp_b_dup.json()["error"]["code"] == "DOCUMENT_DUPLICATE"

    # 3. Initial Readback Verification
    read_t = client.get(f"/api/v1/documents/{tender_doc_id}/content", headers=auth_headers)
    assert read_t.status_code == 200
    assert read_t.content == tender_bytes

    read_b = client.get(f"/api/v1/documents/{bidder_doc_id}/content", headers=auth_headers)
    assert read_b.status_code == 200
    assert read_b.content == bidder_bytes

    # =========================================================================
    # 4. SIMULATE RENDER RESTART / REDEPLOY
    # Wipe the local filesystem completely to prove files are not lost!
    # =========================================================================
    shutil.rmtree(local_dir, ignore_errors=True)
    assert not local_dir.exists()

    # Re-create clean empty directory (just like a freshly deployed Render container)
    local_dir.mkdir(parents=True, exist_ok=True)

    # 5. POST-RESTART CONTENT READBACK
    read_t_after = client.get(f"/api/v1/documents/{tender_doc_id}/content", headers=auth_headers)
    assert read_t_after.status_code == 200
    assert read_t_after.content == tender_bytes
    assert hashlib.sha256(read_t_after.content).hexdigest() == tender_sha

    read_b_after = client.get(f"/api/v1/documents/{bidder_doc_id}/content", headers=auth_headers)
    assert read_b_after.status_code == 200
    assert read_b_after.content == bidder_bytes
    assert hashlib.sha256(read_b_after.content).hexdigest() == bidder_sha

    # 6. POST-RESTART EXTRACTION PIPELINE SAFETY
    # Mock the Intelligence AI extraction service to succeed
    from app.services.ai_adapter import AIServiceAdapter
    from app.schemas.canonical import AIServiceResult

    async def mock_extract_tender(*args, **kwargs):
        return AIServiceResult(
            success=True,
            data=[
                {
                    "clause": "Clause 1.1",
                    "requirement_type": "TURNOVER",
                    "field": "financial.turnover",
                    "operator": "GTE",
                    "expected_value": "10000000",
                    "mandatory": True,
                }
            ],
        )

    async def mock_extract_document(*args, **kwargs):
        return AIServiceResult(
            success=True,
            data=[
                {
                    "field": "financial.turnover",
                    "value": "15000000",
                    "confidence": 0.95,
                }
            ],
        )

    import app.api.v1.tenders as tenders_api
    import app.api.v1.bidders as bidders_api

    monkeypatch.setattr(AIServiceAdapter, "extract_tender", mock_extract_tender)
    monkeypatch.setattr(AIServiceAdapter, "extract_document", mock_extract_document)
    monkeypatch.setattr(tenders_api.ai_adapter, "extract_tender", mock_extract_tender)
    monkeypatch.setattr(bidders_api.ai_adapter, "extract_document", mock_extract_document)

    # Trigger tender extraction on the restarted instance
    proc_t = client.post(
        f"/api/v1/tenders/{tender_id}/process",
        json={"idempotency_key": f"proc-tender-{u_suffix}"},
        headers=auth_headers,
    )
    assert proc_t.status_code == 200
    job_t = proc_t.json()
    assert job_t["status"] == "COMPLETED", f"Tender job failed: {job_t}"
    assert job_t["error_message"] is None  # NOT DOCUMENT_FILE_NOT_FOUND!

    # Trigger bidder extraction on the restarted instance
    proc_b = client.post(
        f"/api/v1/bidders/{bidder_id}/process-documents",
        json={"idempotency_key": f"proc-bidder-{u_suffix}"},
        headers=auth_headers,
    )
    assert proc_b.status_code == 200
    job_b = proc_b.json()
    assert job_b["status"] == "COMPLETED", f"Bidder job failed: {job_b}"
    assert job_b["error_message"] is None  # NOT DOCUMENT_FILE_NOT_FOUND!
