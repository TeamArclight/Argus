import pytest
import httpx
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.session import Base, engine, get_db
from app.main import app
from app.models.domain import (
    AuditEvent,
    Bidder,
    Document,
    ExtractedFact,
    ProcessingJob,
    RuleEvaluation,
    Tender,
    TenderRequirement,
)
from app.schemas.canonical import (
    DocumentType,
    HumanDecisionStatus,
    JobStatus,
    OperatorEnum,
    RequirementType,
    UserRole,
)
from app.services.ai_adapter import AIServiceAdapter
from app.services.document_service import DocumentService
from app.storage.factory import get_storage_provider


@pytest.fixture(autouse=True)
def setup_database_and_storage(monkeypatch, tmp_path):
    storage_dir = tmp_path / "test_storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "ARGUS_STORAGE_LOCAL_PATH", str(storage_dir))
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "http://intelligence.test/api/v1/extract/tender")
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", "http://intelligence.test/api/v1/extract/bidder-document")
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_API_KEY", "test_intel_key")
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def get_auth_headers(role: UserRole = UserRole.PROCUREMENT_OFFICER) -> dict[str, str]:
    from app.auth.tokens import create_access_token
    token = create_access_token(
        user_id=f"user_{role.value.lower()}",
        role=role,
        email=f"{role.value.lower()}@argus.gov.in",
    )
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. AI ADAPTER UNIT & ERROR HANDLING TESTS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_adapter_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", None)
    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(tender_id="t1", document_id="d1")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_ai_adapter_tender_extraction_success(monkeypatch):
    adapter = AIServiceAdapter()

    async def mock_post(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "status": "COMPLETED",
                "provider_model": "gemini-2.5-pro",
                "requirements": [
                    {
                        "clause": "4.1",
                        "requirement_type": "TURNOVER",
                        "field": "financial.average_annual_turnover",
                        "operator": "GTE",
                        "expected_value": 1000000.0,
                        "unit": "INR",
                        "mandatory": True,
                        "confidence": 0.95,
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    res = await adapter.extract_tender(
        tender_id="t1",
        document_id="doc1",
        document_sha256="abc123sha",
        file_bytes=b"%PDF-1.4 Test Tender Document Content",
        filename="tender.pdf",
        content_type="application/pdf",
    )

    assert res.success is True
    assert len(res.data) == 1
    assert res.data[0]["field"] == "financial.average_annual_turnover"
    assert res.data[0]["metadata_json"]["document_id"] == "doc1"
    assert res.data[0]["metadata_json"]["document_sha256"] == "abc123sha"


@pytest.mark.asyncio
async def test_ai_adapter_tender_extraction_http_errors(monkeypatch):
    adapter = AIServiceAdapter()

    # 400 Bad Request -> AI_SERVICE_REQUEST_REJECTED
    async def mock_post_400(self, url, headers=None, json=None):
        return httpx.Response(400, json={"error": "Bad payload"})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_400)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_REQUEST_REJECTED"

    # 401 Unauthorized -> AI_SERVICE_AUTH_ERROR
    async def mock_post_401(self, url, headers=None, json=None):
        return httpx.Response(401, json={"error": "Unauthorized"})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_401)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_AUTH_ERROR"

    # 404 Not Found -> AI_SERVICE_ENDPOINT_NOT_FOUND
    async def mock_post_404(self, url, headers=None, json=None):
        return httpx.Response(404)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_404)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_ENDPOINT_NOT_FOUND"

    # 500 Internal Server Error -> AI_SERVICE_UNAVAILABLE
    async def mock_post_500(self, url, headers=None, json=None):
        return httpx.Response(500)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_500)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"

    # Invalid JSON -> SCHEMA_VALIDATION_FAILED
    async def mock_post_invalid(self, url, headers=None, json=None):
        return httpx.Response(200, text="NOT_JSON_PAYLOAD")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_invalid)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1")
    assert res.success is False
    assert res.error_code == "SCHEMA_VALIDATION_FAILED"


# ---------------------------------------------------------------------------
# 2. TENDER REQUIREMENT EXTRACTION & APPROVAL POLICY TESTS
# ---------------------------------------------------------------------------

def test_process_tender_requirements_extraction_flow(monkeypatch):
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    client = TestClient(app)

    # Step 1: Create Tender
    t_res = client.post(
        "/api/v1/tenders",
        json={"tender_number": "GEM/2026/AI/001", "title": "AI Extraction Test Tender"},
        headers=headers,
    )
    assert t_res.status_code == 201
    tender_id = t_res.json()["id"]

    # Step 2: Upload Tender Document
    pdf_bytes = b"%PDF-1.4 Tender Notice Document Content Bytes"
    up_res = client.post(
        f"/api/v1/tenders/{tender_id}/documents",
        headers=headers,
        files={"file": ("tender_notice.pdf", pdf_bytes, "application/pdf")},
        data={"document_type": DocumentType.TENDER.value},
    )
    assert up_res.status_code == 201
    doc_id = up_res.json()["id"]

    # Step 3: Mock AI Service Response
    async def mock_post_tender(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "status": "COMPLETED",
                "provider_model": "gemini-2.5-pro",
                "requirements": [
                    {
                        "clause": "3.1",
                        "requirement_type": "TURNOVER",
                        "field": "financial.average_annual_turnover",
                        "operator": "GTE",
                        "expected_value": 2500000.0,
                        "unit": "INR",
                        "mandatory": True,
                        "confidence": 0.95,
                    },
                    {
                        "clause": "3.2",
                        "requirement_type": "EXPERIENCE",
                        "field": "experience.similar_projects_count",
                        "operator": "COUNT_GTE",
                        "expected_value": 3,
                        "mandatory": False,
                        "confidence": 0.70,
                    },
                ],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_tender)

    # Step 4: Process Tender
    proc_res = client.post(f"/api/v1/tenders/{tender_id}/process", headers=headers)
    assert proc_res.status_code == 200
    job_json = proc_res.json()
    assert job_json["status"] == JobStatus.COMPLETED.value

    # Step 5: Verify Persisted Requirements & Approval Policy
    req_res = client.get(f"/api/v1/tenders/{tender_id}/requirements", headers=headers)
    assert req_res.status_code == 200
    reqs = req_res.json()
    assert len(reqs) == 2

    high_conf = [r for r in reqs if r["field"] == "financial.average_annual_turnover"][0]
    low_conf = [r for r in reqs if r["field"] == "experience.similar_projects_count"][0]

    assert high_conf["is_approved"] is True
    assert low_conf["is_approved"] is False
    assert high_conf["document_id"] == doc_id


# ---------------------------------------------------------------------------
# 3. BIDDER DOCUMENT FACT EXTRACTION & PARTIAL FAILURE TESTS
# ---------------------------------------------------------------------------

def test_process_bidder_documents_extraction_and_partial_failure(monkeypatch):
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    client = TestClient(app)

    # Create Tender and Bidder
    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/AI/002", "title": "Bidder Extraction Tender"}, headers=headers)
    tender_id = t_res.json()["id"]

    b_res = client.post(f"/api/v1/tenders/{tender_id}/bidders", json={"bidder_name": "ACME AI Corp", "gstin": "27AAACA12341ZV"}, headers=headers)
    bidder_id = b_res.json()["id"]

    # Upload 2 bidder documents
    doc1_res = client.post(
        f"/api/v1/bidders/{bidder_id}/documents",
        headers=headers,
        files={"file": ("gst_cert.pdf", b"%PDF-1.4 Valid GST Certificate Bytes", "application/pdf")},
        data={"document_type": DocumentType.GST_CERT.value},
    )
    doc1_id = doc1_res.json()["id"]

    doc2_res = client.post(
        f"/api/v1/bidders/{bidder_id}/documents",
        headers=headers,
        files={"file": ("unreadable_doc.pdf", b"%PDF-1.4 Corrupted Doc Bytes", "application/pdf")},
        data={"document_type": DocumentType.TURNOVER_CERT.value},
    )
    doc2_id = doc2_res.json()["id"]

    call_counter = {"count": 0}

    async def mock_post_bidder(self, url, headers=None, json=None):
        call_counter["count"] += 1
        if json.get("document_id") == doc1_id:
            return httpx.Response(
                200,
                json={
                    "status": "COMPLETED",
                    "provider_model": "gemini-2.5-pro",
                    "facts": [
                        {
                            "field": "gstin",
                            "value": "27AAACA12341ZV",
                            "source_page": 1,
                            "source_text": "GSTIN: 27AAACA12341ZV",
                            "confidence": 0.99,
                        }
                    ],
                },
            )
        return httpx.Response(500, json={"error": "OCR failure on doc2"})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_bidder)

    # Trigger Bidder Extraction
    proc_res = client.post(f"/api/v1/bidders/{bidder_id}/process-documents", headers=headers)
    assert proc_res.status_code == 200
    job = proc_res.json()

    assert job["status"] == JobStatus.COMPLETED.value
    assert "Partial extraction completion" in job["error_message"]


# ---------------------------------------------------------------------------
# 4. RBAC & PERMISSION TESTS
# ---------------------------------------------------------------------------

def test_extraction_rbac_enforcement():
    client = TestClient(app)

    # Reviewer cannot trigger processing
    reviewer_headers = get_auth_headers(UserRole.REVIEWER)
    res = client.post("/api/v1/tenders/dummy_id/process", headers=reviewer_headers)
    assert res.status_code == 403

    res2 = client.post("/api/v1/bidders/dummy_id/process-documents", headers=reviewer_headers)
    assert res2.status_code == 403

    # Auditor cannot trigger processing
    auditor_headers = get_auth_headers(UserRole.AUDITOR)
    res3 = client.post("/api/v1/tenders/dummy_id/process", headers=auditor_headers)
    assert res3.status_code == 403

    # Anonymous request rejected
    res4 = client.post("/api/v1/tenders/dummy_id/process")
    assert res4.status_code == 401
