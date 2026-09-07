from datetime import datetime, timezone
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


def get_auth_headers(
    role: UserRole = UserRole.PROCUREMENT_OFFICER,
    user_id: str | None = None,
    name: str | None = None,
) -> dict[str, str]:
    from app.auth.tokens import create_access_token
    uid = user_id or f"user_{role.value.lower()}"
    token = create_access_token(
        user_id=uid,
        role=role,
        name=name or uid,
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
                "contract_version": "1.0",
                "request_id": (json or {}).get("request_id", "req123"),
                "document_id": (json or {}).get("document_id", "doc1"),
                "document_sha256": (json or {}).get("document_sha256", "abc123sha"),
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
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_REQUEST_REJECTED"

    # 401 Unauthorized -> AI_SERVICE_AUTH_ERROR
    async def mock_post_401(self, url, headers=None, json=None):
        return httpx.Response(401, json={"error": "Unauthorized"})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_401)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_AUTH_ERROR"

    # 404 Not Found -> AI_SERVICE_ENDPOINT_NOT_FOUND
    async def mock_post_404(self, url, headers=None, json=None):
        return httpx.Response(404)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_404)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_ENDPOINT_NOT_FOUND"

    # 500 Internal Server Error -> AI_SERVICE_UNAVAILABLE
    async def mock_post_500(self, url, headers=None, json=None):
        return httpx.Response(500)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_500)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"

    # Invalid JSON -> SCHEMA_VALIDATION_FAILED
    async def mock_post_invalid(self, url, headers=None, json=None):
        return httpx.Response(200, text="NOT_JSON_PAYLOAD")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_invalid)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes")
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
                "contract_version": "1.0",
                "request_id": (json or {}).get("request_id", "req123"),
                "document_id": (json or {}).get("document_id", "doc1"),
                "document_sha256": (json or {}).get("document_sha256", "abc"),
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

    # All AI-extracted requirements default to is_approved = False regardless of confidence (0.95 vs 0.70)
    for r in reqs:
        assert r["is_approved"] is False


def test_ai_extraction_scrubs_model_supplied_authority(monkeypatch):
    """Test that model-supplied is_approved/approved_by/approved_at fields are scrubbed and ignored."""
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    client = TestClient(app)

    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/AI/SCRUB", "title": "Authority Scrub Tender"}, headers=headers)
    tender_id = t_res.json()["id"]

    pdf_bytes = b"%PDF-1.4 Tender Document Bytes"
    client.post(
        f"/api/v1/tenders/{tender_id}/documents",
        headers=headers,
        files={"file": ("tender.pdf", pdf_bytes, "application/pdf")},
        data={"document_type": DocumentType.TENDER.value},
    )

    async def mock_post_malicious_model(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": (json or {}).get("request_id", "req123"),
                "document_id": (json or {}).get("document_id", "doc1"),
                "document_sha256": (json or {}).get("document_sha256", "abc"),
                "status": "COMPLETED",
                "provider_model": "rogue-llm-1.0",
                "requirements": [
                    {
                        "clause": "1.1",
                        "requirement_type": "GST",
                        "field": "general.gstin",
                        "operator": "EXISTS",
                        "expected_value": True,
                        "confidence": 1.0,
                        "is_approved": True,  # Model tries to self-approve
                        "approved_by": "ATTACKER_AI",
                        "approved_at": "2026-01-01T00:00:00Z",
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_malicious_model)

    client.post(f"/api/v1/tenders/{tender_id}/process", headers=headers)

    req_res = client.get(f"/api/v1/tenders/{tender_id}/requirements", headers=headers)
    assert req_res.status_code == 200
    reqs = req_res.json()
    assert len(reqs) == 1
    req = reqs[0]

    assert req["is_approved"] is False  # Confidence 1.0 & model is_approved=True IGNORED
    meta = req.get("metadata_json") or {}
    assert meta.get("approved_by") != "ATTACKER_AI"  # Scrubbed


def test_officer_approval_workflow_and_idempotency(monkeypatch):
    """Test officer requirement approval, JWT principal attribution, and repeated approval idempotency."""
    officer_headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER, user_id="OFFICER-7711", name="Anita Desai")
    client = TestClient(app)

    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/AI/APPR", "title": "Approval Test Tender"}, headers=officer_headers)
    tender_id = t_res.json()["id"]

    pdf_bytes = b"%PDF-1.4 Tender Document Bytes"
    client.post(
        f"/api/v1/tenders/{tender_id}/documents",
        headers=officer_headers,
        files={"file": ("tender.pdf", pdf_bytes, "application/pdf")},
        data={"document_type": DocumentType.TENDER.value},
    )

    async def mock_post_tender(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": (json or {}).get("request_id", "req123"),
                "document_id": (json or {}).get("document_id", "doc1"),
                "document_sha256": (json or {}).get("document_sha256", "abc"),
                "status": "COMPLETED",
                "provider_model": "gemini-2.5-pro",
                "requirements": [
                    {
                        "clause": "2.1",
                        "requirement_type": "GST",
                        "field": "general.gstin",
                        "operator": "EXISTS",
                        "expected_value": True,
                        "confidence": 0.88,
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_tender)
    client.post(f"/api/v1/tenders/{tender_id}/process", headers=officer_headers)

    reqs = client.get(f"/api/v1/tenders/{tender_id}/requirements", headers=officer_headers).json()
    req_id = reqs[0]["id"]
    assert reqs[0]["is_approved"] is False

    # Officer approves requirement
    appr_res = client.post(f"/api/v1/tenders/{tender_id}/requirements/{req_id}/approve", headers=officer_headers)
    assert appr_res.status_code == 200
    appr_json = appr_res.json()
    assert appr_json["is_approved"] is True
    meta = appr_json["metadata_json"]
    assert meta["approved_by"] == "OFFICER-7711"
    assert "approved_at" in meta
    first_appr_at = meta["approved_at"]

    # Idempotent approval check with different officer header
    officer2_headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER, user_id="OFFICER-9999")
    appr2_res = client.post(f"/api/v1/tenders/{tender_id}/requirements/{req_id}/approve", headers=officer2_headers)
    assert appr2_res.status_code == 200
    appr2_json = appr2_res.json()
    assert appr2_json["is_approved"] is True
    meta2 = appr2_json["metadata_json"]
    assert meta2["approved_by"] == "OFFICER-7711"  # Preserves original officer attribution
    assert meta2["approved_at"] == first_appr_at


def test_approval_rbac_and_validation():
    """Test RBAC restrictions and validation on approval endpoint."""
    admin_headers = get_auth_headers(UserRole.ADMIN, user_id="ADMIN-001")
    client = TestClient(app)

    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/AI/VAL", "title": "Validation Tender"}, headers=admin_headers)
    tender_id = t_res.json()["id"]

    # Manual creation by Admin (is_approved=True)
    req_res = client.post(
        f"/api/v1/tenders/{tender_id}/requirements",
        json={
            "clause": "1.1",
            "requirement_type": "GST",
            "field": "general.gstin",
            "operator": "EXISTS",
            "expected_value": True,
        },
        headers=admin_headers,
    )
    assert req_res.status_code == 201
    req_json = req_res.json()
    assert req_json["is_approved"] is True
    assert req_json["metadata_json"]["approved_by"] == "ADMIN-001"

    # Reviewer cannot approve
    rev_headers = get_auth_headers(UserRole.REVIEWER)
    res_rev = client.post(f"/api/v1/tenders/{tender_id}/requirements/{req_json['id']}/approve", headers=rev_headers)
    assert res_rev.status_code == 403

    # Auditor cannot approve
    aud_headers = get_auth_headers(UserRole.AUDITOR)
    res_aud = client.post(f"/api/v1/tenders/{tender_id}/requirements/{req_json['id']}/approve", headers=aud_headers)
    assert res_aud.status_code == 403

    # Anonymous cannot approve
    res_anon = client.post(f"/api/v1/tenders/{tender_id}/requirements/{req_json['id']}/approve")
    assert res_anon.status_code == 401


def test_compliance_execution_zero_approved_rules_fallback(monkeypatch):
    """Test that zero approved rules results in UNKNOWN status and NO_APPROVED_REQUIREMENTS reason code."""
    officer_headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER, user_id="OFFICER-4400")
    client = TestClient(app)

    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/AI/ZERO", "title": "Zero Approved Tender"}, headers=officer_headers)
    tender_id = t_res.json()["id"]

    b_res = client.post(
        f"/api/v1/tenders/{tender_id}/bidders",
        json={"bidder_name": "Zero Approved Corp", "gstin": "27AAACA12341ZV", "metadata_json": {"verification_mode": "demo"}},
        headers=officer_headers,
    )
    bidder_id = b_res.json()["id"]

    pdf_bytes = b"%PDF-1.4 Tender Document Bytes"
    client.post(
        f"/api/v1/tenders/{tender_id}/documents",
        headers=officer_headers,
        files={"file": ("tender.pdf", pdf_bytes, "application/pdf")},
        data={"document_type": DocumentType.TENDER.value},
    )

    async def mock_post_tender(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": (json or {}).get("request_id", "req123"),
                "document_id": (json or {}).get("document_id", "doc1"),
                "document_sha256": (json or {}).get("document_sha256", "abc"),
                "status": "COMPLETED",
                "provider_model": "gemini-2.5-pro",
                "requirements": [
                    {
                        "clause": "1.1",
                        "requirement_type": "GST",
                        "field": "general.gstin",
                        "operator": "EXISTS",
                        "expected_value": True,
                        "confidence": 0.99,
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_tender)
    client.post(f"/api/v1/tenders/{tender_id}/process", headers=officer_headers)

    # All extractions are candidate unapproved rules
    reqs = client.get(f"/api/v1/tenders/{tender_id}/requirements", headers=officer_headers).json()
    assert len(reqs) == 1
    assert reqs[0]["is_approved"] is False

    # Trigger bidder verification & compliance evaluation
    v_res = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=officer_headers)
    assert v_res.status_code == 200

    # Fetch compliance overview
    c_res = client.get(f"/api/v1/bidders/{bidder_id}/compliance", headers=officer_headers)
    assert c_res.status_code == 200
    comp = c_res.json()

    assert comp["overall_status"] == "UNKNOWN"
    assert len(comp["rule_evaluations"]) == 0

    # Now approve requirement and re-run evaluation
    appr_res = client.post(f"/api/v1/tenders/{tender_id}/requirements/{reqs[0]['id']}/approve", headers=officer_headers)
    assert appr_res.status_code == 200

    v2_res = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=officer_headers)
    assert v2_res.status_code == 200

    c2_res = client.get(f"/api/v1/bidders/{bidder_id}/compliance", headers=officer_headers)
    assert c2_res.status_code == 200
    comp2 = c2_res.json()

    assert len(comp2["rule_evaluations"]) == 1
    assert comp2["overall_status"] == "PASS"


def test_compliance_engine_rejects_unapproved_canonical_rule():
    """Test that pure ComplianceEngine raises ValueError when given an unapproved candidate rule."""
    from app.compliance.engine import ComplianceEngine
    from app.schemas.canonical import TenderRequirementRead, RequirementType, OperatorEnum

    unapproved_rule = TenderRequirementRead(
        id="req_unapp_123",
        tender_id="t_test",
        clause="1.1",
        requirement_type=RequirementType.GST,
        field="general.gstin",
        operator=OperatorEnum.EXISTS,
        expected_value=True,
        confidence=0.99,
        is_approved=False,
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValueError, match="ComplianceEngine cannot evaluate unapproved requirement candidate"):
        ComplianceEngine.evaluate(rule=unapproved_rule, facts=[], verification_results=[])


def test_reprocessing_preserves_approved_requirements(monkeypatch):
    """Test that reprocessing does not mutate or delete existing approved requirements."""
    officer_headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER, user_id="OFFICER-5511")
    client = TestClient(app)

    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/AI/REPROC", "title": "Reprocess Preserv Tender"}, headers=officer_headers)
    tender_id = t_res.json()["id"]

    pdf_bytes = b"%PDF-1.4 Tender Document Bytes"
    client.post(
        f"/api/v1/tenders/{tender_id}/documents",
        headers=officer_headers,
        files={"file": ("tender.pdf", pdf_bytes, "application/pdf")},
        data={"document_type": DocumentType.TENDER.value},
    )

    # 1. Manual requirement creation (Approved)
    man_req = client.post(
        f"/api/v1/tenders/{tender_id}/requirements",
        json={
            "clause": "1.0",
            "requirement_type": "GST",
            "field": "general.gstin",
            "operator": "EXISTS",
            "expected_value": True,
        },
        headers=officer_headers,
    ).json()
    assert man_req["is_approved"] is True

    # 2. Extract requirements via AI
    async def mock_post_tender(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": (json or {}).get("request_id", "req123"),
                "document_id": (json or {}).get("document_id", "doc1"),
                "document_sha256": (json or {}).get("document_sha256", "abc"),
                "status": "COMPLETED",
                "provider_model": "gemini-2.5-pro",
                "requirements": [
                    {
                        "clause": "2.0",
                        "requirement_type": "TURNOVER",
                        "field": "financial.average_annual_turnover",
                        "operator": "GTE",
                        "expected_value": 1000000.0,
                        "confidence": 0.90,
                    }
                ],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_tender)
    client.post(f"/api/v1/tenders/{tender_id}/process", headers=officer_headers)

    reqs = client.get(f"/api/v1/tenders/{tender_id}/requirements", headers=officer_headers).json()
    assert len(reqs) == 2

    approved_reqs = [r for r in reqs if r["is_approved"] is True]
    unapproved_reqs = [r for r in reqs if r["is_approved"] is False]

    assert len(approved_reqs) == 1
    assert approved_reqs[0]["id"] == man_req["id"]
    assert len(unapproved_reqs) == 1

    # 3. Reprocess tender again
    client.post(f"/api/v1/tenders/{tender_id}/process", headers=officer_headers)

    reqs2 = client.get(f"/api/v1/tenders/{tender_id}/requirements", headers=officer_headers).json()
    assert len(reqs2) == 2  # Deduplicated, no duplicates created, manual approved requirement preserved intact
    assert any(r["id"] == man_req["id"] and r["is_approved"] is True for r in reqs2)


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
                    "contract_version": "1.0",
                    "request_id": (json or {}).get("request_id", "req123"),
                    "document_id": (json or {}).get("document_id", doc1_id),
                    "document_sha256": (json or {}).get("document_sha256", "abc"),
                    "bidder_id": (json or {}).get("bidder_id", bidder_id),
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

    assert job["status"] == JobStatus.REVIEW_REQUIRED.value
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


# ---------------------------------------------------------------------------
# 5. FINAL BOUNDARY HARDENING & REGRESSION TESTS
# ---------------------------------------------------------------------------

def test_rule_validator_semantics():
    from app.services.rule_validator import RuleValidator, RuleValidationError

    # 1. Valid EXISTS rule with boolean
    val1 = RuleValidator.validate({
        "clause": "1.1",
        "requirement_type": "GST",
        "field": "general.gstin",
        "operator": "EXISTS",
        "expected_value": True,
        "confidence": 0.95,
    })
    assert val1["clause"] == "1.1"
    assert val1["operator"] == OperatorEnum.EXISTS

    # 2. Valid GTE rule with float
    val2 = RuleValidator.validate({
        "clause": "2.1",
        "requirement_type": "TURNOVER",
        "field": "financial.turnover",
        "operator": "GTE",
        "expected_value": 5000000.0,
        "confidence": 1.0,
    })
    assert val2["expected_value"] == 5000000.0

    # 3. Invalid GTE rule with None expected_value -> raises RuleValidationError
    with pytest.raises(RuleValidationError, match="requires a non-null numeric threshold"):
        RuleValidator.validate({
            "clause": "2.1",
            "requirement_type": "TURNOVER",
            "field": "financial.turnover",
            "operator": "GTE",
            "expected_value": None,
        })

    # 4. Invalid expected_value NaN / Infinity
    with pytest.raises(RuleValidationError, match="cannot be NaN or Infinity"):
        RuleValidator.validate({
            "clause": "2.1",
            "requirement_type": "TURNOVER",
            "field": "financial.turnover",
            "operator": "GTE",
            "expected_value": float("nan"),
        })

    # 5. Invalid confidence out of bounds
    with pytest.raises(RuleValidationError, match="confidence must be a finite number between 0.0 and 1.0"):
        RuleValidator.validate({
            "clause": "2.1",
            "requirement_type": "TURNOVER",
            "field": "financial.turnover",
            "operator": "GTE",
            "expected_value": 100.0,
            "confidence": 1.5,
        })


@pytest.mark.asyncio
async def test_ai_adapter_envelope_mismatches(monkeypatch):
    adapter = AIServiceAdapter()

    # Case 1: Contract version mismatch ("2.0")
    async def mock_post_bad_contract(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "2.0",
                "request_id": json["request_id"],
                "document_id": json["document_id"],
                "document_sha256": json["document_sha256"],
                "status": "SUCCESS",
                "requirements": [],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_bad_contract)
    res1 = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes", request_id="req123")
    assert res1.success is False
    assert res1.error_code == "CONTRACT_MISMATCH"

    # Case 2: Request ID mismatch
    async def mock_post_bad_req_id(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": "WRONG_REQ_ID",
                "document_id": json["document_id"],
                "document_sha256": json["document_sha256"],
                "status": "SUCCESS",
                "requirements": [],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_bad_req_id)
    res2 = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes", request_id="req123")
    assert res2.success is False
    assert res2.error_code == "REQUEST_ID_MISMATCH"

    # Case 3: Document ID mismatch
    async def mock_post_bad_doc_id(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": json["request_id"],
                "document_id": "WRONG_DOC_ID",
                "document_sha256": json["document_sha256"],
                "status": "SUCCESS",
                "requirements": [],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_bad_doc_id)
    res3 = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes", request_id="req123")
    assert res3.success is False
    assert res3.error_code == "DOCUMENT_ID_MISMATCH"


@pytest.mark.asyncio
async def test_ai_adapter_read_timeout_not_retried(monkeypatch):
    adapter = AIServiceAdapter()
    call_attempts = {"count": 0}

    async def mock_post_read_timeout(self, url, headers=None, json=None):
        call_attempts["count"] += 1
        raise httpx.ReadTimeout("Read timed out after 15s")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_read_timeout)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert res.retryable is False
    assert call_attempts["count"] == 1  # Read timeout is NOT retried!


def test_process_tender_missing_document_fails():
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    client = TestClient(app)

    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/NO_DOC_ROW/001", "title": "No Doc Row Tender"}, headers=headers)
    tender_id = t_res.json()["id"]

    proc_res = client.post(f"/api/v1/tenders/{tender_id}/process", headers=headers)
    assert proc_res.status_code == 200
    job = proc_res.json()
    assert "Tender processing requires a persisted document record" in job["error_message"]


def test_process_tender_document_sha256_mismatch_fails(monkeypatch):
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    client = TestClient(app)

    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/TAMPER/001", "title": "Tampered Document Tender"}, headers=headers)
    tender_id = t_res.json()["id"]

    pdf_bytes = b"%PDF-1.4 Clean Document Bytes"
    up_res = client.post(
        f"/api/v1/tenders/{tender_id}/documents",
        headers=headers,
        files={"file": ("tender.pdf", pdf_bytes, "application/pdf")},
        data={"document_type": DocumentType.TENDER.value},
    )
    doc_id = up_res.json()["id"]

    # Tamper with recorded doc.sha256 in DB
    db: Session = next(get_db())
    doc = db.query(Document).filter(Document.id == doc_id).first()
    doc.sha256 = "0000000000000000000000000000000000000000000000000000000000000000"
    db.commit()
    db.close()

    proc_res = client.post(f"/api/v1/tenders/{tender_id}/process", headers=headers)
    assert proc_res.status_code == 200
    job = proc_res.json()
    assert job["status"] == JobStatus.FAILED.value
    assert "hash mismatch" in job["error_message"].lower()


def test_create_manual_requirement_cross_tender_document_rejected():
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    client = TestClient(app)

    # Tender 1 & Tender 2
    t1_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/T1/001", "title": "Tender 1"}, headers=headers)
    t1_id = t1_res.json()["id"]

    t2_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/T2/001", "title": "Tender 2"}, headers=headers)
    t2_id = t2_res.json()["id"]

    # Upload doc to Tender 2
    up_res = client.post(
        f"/api/v1/tenders/{t2_id}/documents",
        headers=headers,
        files={"file": ("t2_doc.pdf", b"%PDF-1.4 T2 Doc Content", "application/pdf")},
        data={"document_type": DocumentType.TENDER.value},
    )
    t2_doc_id = up_res.json()["id"]

    # Try creating requirement on Tender 1 referencing Tender 2's document_id
    req_res = client.post(
        f"/api/v1/tenders/{t1_id}/requirements",
        json={
            "clause": "1.1",
            "requirement_type": "GST",
            "field": "general.gstin",
            "operator": "EXISTS",
            "expected_value": True,
            "document_id": t2_doc_id,
        },
        headers=headers,
    )
    assert req_res.status_code == 422
    res_json = req_res.json()
    err_text = str(res_json.get("detail") or res_json.get("error", {}).get("message", ""))
    assert "document does not belong to tender" in err_text


def test_process_bidder_reprocessing_preserves_unreferenced_facts(monkeypatch):
    """Test that reprocessing bidder documents preserves old unreferenced ExtractedFact rows."""
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    client = TestClient(app)

    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/FACT_PRESERV", "title": "Fact Preservation Tender"}, headers=headers)
    tender_id = t_res.json()["id"]

    b_res = client.post(f"/api/v1/tenders/{tender_id}/bidders", json={"bidder_name": "Fact Preservation Corp", "gstin": "27AAACA12341ZV"}, headers=headers)
    bidder_id = b_res.json()["id"]

    doc_res = client.post(
        f"/api/v1/bidders/{bidder_id}/documents",
        headers=headers,
        files={"file": ("cert.pdf", b"%PDF-1.4 Fact Preserv Cert Bytes", "application/pdf")},
        data={"document_type": DocumentType.GST_CERT.value},
    )
    doc_id = doc_res.json()["id"]

    # Manually insert an old ExtractedFact not referenced by any RuleEvaluation evidence_ids
    db: Session = next(get_db())
    old_fact = ExtractedFact(
        bidder_id=bidder_id,
        document_id=doc_id,
        field="historical.unreferenced_field",
        value="OLD_UNREFERENCED_VALUE",
        source_page=1,
        source_text="Old unreferenced text",
        confidence=0.88,
    )
    db.add(old_fact)
    db.commit()
    old_fact_id = old_fact.id
    db.close()

    # Mock AI response with a different new fact
    async def mock_post_bidder(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": (json or {}).get("request_id", "req123"),
                "document_id": (json or {}).get("document_id", doc_id),
                "document_sha256": (json or {}).get("document_sha256", "abc"),
                "bidder_id": (json or {}).get("bidder_id", bidder_id),
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

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_bidder)

    proc_res = client.post(f"/api/v1/bidders/{bidder_id}/process-documents", headers=headers)
    assert proc_res.status_code == 200

    # Verify old_fact is still intact in DB
    db2: Session = next(get_db())
    retrieved_fact = db2.query(ExtractedFact).filter(ExtractedFact.id == old_fact_id).first()
    assert retrieved_fact is not None
    assert retrieved_fact.value == "OLD_UNREFERENCED_VALUE"
    db2.close()


def test_process_bidder_missing_or_corrupt_doc_sha256_fails_closed(monkeypatch):
    """Test that bidder processing fails closed when doc.sha256 is missing or corrupt."""
    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    client = TestClient(app)

    t_res = client.post("/api/v1/tenders", json={"tender_number": "GEM/2026/BIDDER_CORRUPT", "title": "Corrupt Doc Tender"}, headers=headers)
    tender_id = t_res.json()["id"]

    b_res = client.post(f"/api/v1/tenders/{tender_id}/bidders", json={"bidder_name": "Corrupt Doc Corp", "gstin": "27AAACA12341ZV"}, headers=headers)
    bidder_id = b_res.json()["id"]

    doc_res = client.post(
        f"/api/v1/bidders/{bidder_id}/documents",
        headers=headers,
        files={"file": ("doc.pdf", b"%PDF-1.4 Bidder Doc Bytes", "application/pdf")},
        data={"document_type": DocumentType.GST_CERT.value},
    )
    doc_id = doc_res.json()["id"]

    # Clear recorded sha256 in DB
    db: Session = next(get_db())
    doc = db.query(Document).filter(Document.id == doc_id).first()
    doc.sha256 = ""
    db.commit()
    db.close()

    proc_res = client.post(f"/api/v1/bidders/{bidder_id}/process-documents", headers=headers)
    assert proc_res.status_code == 200
    job = proc_res.json()
    assert job["status"] == JobStatus.FAILED.value
    assert "extraction failed for all 1 documents" in job["error_message"].lower()


@pytest.mark.asyncio
async def test_ai_adapter_bidder_envelope_mismatches(monkeypatch):
    adapter = AIServiceAdapter()

    # Case: Bidder ID mismatch
    async def mock_post_bad_bidder_id(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": json["request_id"],
                "document_id": json["document_id"],
                "document_sha256": json["document_sha256"],
                "bidder_id": "WRONG_BIDDER_999",
                "status": "SUCCESS",
                "facts": [],
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_bad_bidder_id)
    res = await adapter.extract_document(
        bidder_id="real_bidder_123",
        document_id="doc1",
        document_type="GST_CERT",
        document_sha256="abc",
        file_bytes=b"bytes",
        filename="f.pdf",
        content_type="application/pdf",
        request_id="req123",
    )
    assert res.success is False
    assert res.error_code == "BIDDER_ID_MISMATCH"


@pytest.mark.asyncio
async def test_ai_adapter_error_message_scrubbing(monkeypatch):
    adapter = AIServiceAdapter()

    async def mock_post_leaky_exception(self, url, headers=None, json=None):
        raise httpx.ConnectError("Failed to connect to http://secret-internal-service.local/key=sk-1234567890secret")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_leaky_exception)
    res = await adapter.extract_tender(tender_id="t1", document_id="doc1", document_sha256="abc", file_bytes=b"bytes")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    # Ensure sensitive string is scrubbed from res.message
    assert "sk-1234567890" not in res.message
    assert "secret-internal-service" not in res.message
    assert res.message == "Intelligence service connection failed."


