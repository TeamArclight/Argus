import hashlib
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import Base, engine, get_db
from app.main import app
from app.models.domain import Bidder, Document, ExtractedFact, Tender
from app.schemas.canonical import RAGQueryRequest, UserRole
from app.services.ai_adapter import AIServiceAdapter
from app.services.rag_adapter import RAGServiceAdapter
from tests.auth_helpers import get_auth_headers


TEST_BYTES = b"%PDF-1.4 Standard Test Document Content"
TEST_SHA256 = hashlib.sha256(TEST_BYTES).hexdigest()


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.mark.asyncio
async def test_base_url_alone_does_not_invent_extract_tender(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", "https://ai.argus.local")
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", None)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(
        tender_id="T1",
        document_id="doc1",
        document_sha256=TEST_SHA256,
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert res.data is None


@pytest.mark.asyncio
async def test_base_url_alone_does_not_invent_extract_document(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", "https://ai.argus.local")
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", None)

    adapter = AIServiceAdapter()
    res = await adapter.extract_document(
        document_id="D1",
        document_sha256=TEST_SHA256,
        bidder_id="B1",
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert res.data is None


@pytest.mark.asyncio
async def test_base_url_alone_does_not_invent_rag_query(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", "https://ai.argus.local")
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_RAG_URL", None)

    adapter = RAGServiceAdapter()
    res = await adapter.retrieve(RAGQueryRequest(query="GFR Rule 144 turnover exemption"))
    assert res.results == []
    assert res.error_code == "RAG_SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_missing_explicit_tender_url_returns_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", None)
    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(
        tender_id="T1",
        document_id="doc1",
        document_sha256=TEST_SHA256,
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_missing_explicit_document_url_returns_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", None)
    adapter = AIServiceAdapter()
    res = await adapter.extract_document(
        document_id="D1",
        document_sha256=TEST_SHA256,
        bidder_id="B1",
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_missing_explicit_rag_url_returns_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_RAG_URL", None)
    adapter = RAGServiceAdapter()
    res = await adapter.retrieve(RAGQueryRequest(query="Exemption query"))
    assert res.results == []
    assert res.error_code == "RAG_SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,expected_ai_code,expected_rag_code,expected_retryable",
    [
        (400, "AI_SERVICE_REQUEST_REJECTED", "RAG_SERVICE_REQUEST_REJECTED", False),
        (401, "AI_SERVICE_AUTH_ERROR", "RAG_SERVICE_AUTH_ERROR", False),
        (403, "AI_SERVICE_AUTH_ERROR", "RAG_SERVICE_AUTH_ERROR", False),
        (404, "AI_SERVICE_ENDPOINT_NOT_FOUND", "RAG_SERVICE_ENDPOINT_NOT_FOUND", False),
        (405, "AI_SERVICE_REQUEST_REJECTED", "RAG_SERVICE_REQUEST_REJECTED", False),
        (422, "AI_SERVICE_REQUEST_REJECTED", "RAG_SERVICE_REQUEST_REJECTED", False),
        (429, "AI_SERVICE_UNAVAILABLE", "RAG_SERVICE_UNAVAILABLE", True),
        (501, "AI_SERVICE_UNAVAILABLE", "RAG_SERVICE_UNAVAILABLE", True),
        (503, "AI_SERVICE_UNAVAILABLE", "RAG_SERVICE_UNAVAILABLE", True),
    ],
)
async def test_http_status_code_classification(
    monkeypatch, status_code, expected_ai_code, expected_rag_code, expected_retryable
):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "https://ai.argus.local/extract-tender")
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_RAG_URL", "https://ai.argus.local/rag-query")

    async def mock_post(self, url, headers=None, json=None):
        req = httpx.Request("POST", url)
        return httpx.Response(status_code, text="Internal detail that should not leak", request=req)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    ai_adapter = AIServiceAdapter()
    ai_res = await ai_adapter.extract_tender(
        tender_id="T1",
        document_id="doc1",
        document_sha256=TEST_SHA256,
        file_bytes=TEST_BYTES,
    )
    assert ai_res.success is False
    assert ai_res.error_code == expected_ai_code
    assert ai_res.retryable == expected_retryable
    assert "Internal detail" not in (ai_res.message or "")

    rag_adapter = RAGServiceAdapter()
    rag_res = await rag_adapter.retrieve(RAGQueryRequest(query="query"))
    assert rag_res.error_code == expected_rag_code
    assert "Internal detail" not in (rag_res.error_message or "")


@pytest.mark.asyncio
async def test_malformed_bidder_fact_payload_returns_schema_validation_failed(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", "https://ai.argus.local/extract-doc")

    # Case 1: missing field
    async def mock_post_missing_field(self, url, headers=None, json=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": json["request_id"],
                "document_id": json["document_id"],
                "document_sha256": json["document_sha256"],
                "bidder_id": json["bidder_id"],
                "status": "SUCCESS",
                "facts": [{"value": "123"}],
            },
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_missing_field)
    adapter = AIServiceAdapter()
    res1 = await adapter.extract_document(
        document_id="D1",
        document_sha256=TEST_SHA256,
        bidder_id="B1",
        file_bytes=TEST_BYTES,
    )
    assert res1.success is False
    assert res1.error_code == "SCHEMA_VALIDATION_FAILED"

    # Case 2: confidence out of bounds (> 1.0)
    async def mock_post_bad_confidence(self, url, headers=None, json=None):
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": json["request_id"],
                "document_id": json["document_id"],
                "document_sha256": json["document_sha256"],
                "bidder_id": json["bidder_id"],
                "status": "SUCCESS",
                "facts": [{"field": "gstin", "value": "27A", "confidence": 1.5}],
            },
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_bad_confidence)
    res2 = await adapter.extract_document(
        document_id="D1",
        document_sha256=TEST_SHA256,
        bidder_id="B1",
        file_bytes=TEST_BYTES,
    )
    assert res2.success is False
    assert res2.error_code == "SCHEMA_VALIDATION_FAILED"


def test_intelligence_health_endpoint_configurations(monkeypatch):
    with TestClient(app) as client:
        # 1. No endpoint URLs -> configured = False
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", None)
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", None)
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_RAG_URL", None)
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", None)
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_API_KEY", None)

        res = client.get("/health/integrations").json()["intelligence"]
        assert res["configured"] is False
        assert "unconfigured" in res["details"].lower()

        # 2. BASE_URL only -> configured = False
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", "https://ai.argus.local")
        res = client.get("/health/integrations").json()["intelligence"]
        assert res["configured"] is False
        assert "unconfigured" in res["details"].lower()

        # 3. API key only -> configured = False
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", None)
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_API_KEY", "secret-key")
        res = client.get("/health/integrations").json()["intelligence"]
        assert res["configured"] is False
        assert "unconfigured" in res["details"].lower()

        # 4. Tender URL only -> configured = True
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_API_KEY", None)
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "https://ai.argus.local/tender")
        res = client.get("/health/integrations").json()["intelligence"]
        assert res["configured"] is True
        assert "tender extraction" in res["details"]

        # 5. Document URL only -> configured = True
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", None)
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", "https://ai.argus.local/doc")
        res = client.get("/health/integrations").json()["intelligence"]
        assert res["configured"] is True
        assert "document extraction" in res["details"]

        # 6. RAG URL only -> configured = True
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", None)
        monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_RAG_URL", "https://ai.argus.local/rag")
        res = client.get("/health/integrations").json()["intelligence"]
        assert res["configured"] is True
        assert "RAG" in res["details"]


def test_tender_processing_without_raw_document_uri_fails():
    headers = get_auth_headers(UserRole.ADMIN)
    with TestClient(app) as client:
        t_payload = {
            "tender_number": "GEM/2026/NO_DOC/001",
            "title": "Tender Missing Raw Document URI",
            "raw_document_uri": None,
        }
        resp = client.post("/api/v1/tenders", json=t_payload, headers=headers)
        assert resp.status_code == 201
        tender_id = resp.json()["id"]

        proc_resp = client.post(f"/api/v1/tenders/{tender_id}/process", headers=headers)
        assert proc_resp.status_code == 200
        job = proc_resp.json()
        assert "Tender processing requires a persisted document record" in job["error_message"]


def test_bidder_creation_creates_no_documents_or_extracted_facts():
    headers = get_auth_headers(UserRole.ADMIN)
    with TestClient(app) as client:
        t_payload = {
            "tender_number": "GEM/2026/BIDDER_TEST/001",
            "title": "Tender for Bidder Creation Test",
            "raw_document_uri": "s3://tenders/doc.pdf",
        }
        t_resp = client.post("/api/v1/tenders", json=t_payload, headers=headers).json()
        tender_id = t_resp["id"]

        b_payload = {
            "bidder_name": "Clean Entity Pvt Ltd",
            "gstin": "27AAAAA0000A1Z5",
        }
        b_resp = client.post(f"/api/v1/tenders/{tender_id}/bidders", json=b_payload, headers=headers)
        assert b_resp.status_code == 201
        bidder_id = b_resp.json()["id"]

        db: Session = next(get_db())
        doc_count = db.query(Document).filter(Document.bidder_id == bidder_id).count()
        fact_count = db.query(ExtractedFact).filter(ExtractedFact.bidder_id == bidder_id).count()
        db.close()

        assert doc_count == 0
        assert fact_count == 0


@pytest.mark.asyncio
async def test_ai_timeout_returns_structured_failure(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "https://ai.argus.local/extract-tender")

    async def mock_post(self, url, headers=None, json=None):
        raise httpx.TimeoutException("AI Gateway timeout")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(
        tender_id="T1",
        document_id="doc1",
        document_sha256=TEST_SHA256,
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert "timed out" in res.message.lower()


@pytest.mark.asyncio
async def test_ai_connection_error_returns_structured_failure(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "https://ai.argus.local/extract-tender")

    async def mock_post(self, url, headers=None, json=None):
        raise httpx.RequestError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(
        tender_id="T1",
        document_id="doc1",
        document_sha256=TEST_SHA256,
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert "transport failure" in res.message.lower()


# ---------------------------------------------------------------------------
# ADAPTER INPUT VALIDATION & LEGACY SHAPE REJECTION TESTS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adapter_rejects_missing_document_id(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "https://ai.argus.local/extract-tender")
    did_dispatch = {"called": False}

    async def mock_post(self, url, headers=None, json=None):
        did_dispatch["called"] = True
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(
        tender_id="T1",
        document_id="",
        document_sha256=TEST_SHA256,
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_REQUEST_REJECTED"
    assert "Missing required document_id" in res.message
    assert did_dispatch["called"] is False


@pytest.mark.asyncio
async def test_adapter_rejects_invalid_document_sha256(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "https://ai.argus.local/extract-tender")
    did_dispatch = {"called": False}

    async def mock_post(self, url, headers=None, json=None):
        did_dispatch["called"] = True
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(
        tender_id="T1",
        document_id="doc1",
        document_sha256="not_a_64_hex_sha256",
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_REQUEST_REJECTED"
    assert "64-character hex" in res.message
    assert did_dispatch["called"] is False


@pytest.mark.asyncio
async def test_adapter_rejects_missing_or_empty_file_bytes(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "https://ai.argus.local/extract-tender")
    did_dispatch = {"called": False}

    async def mock_post(self, url, headers=None, json=None):
        did_dispatch["called"] = True
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(
        tender_id="T1",
        document_id="doc1",
        document_sha256=TEST_SHA256,
        file_bytes=b"",
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_REQUEST_REJECTED"
    assert "Missing or empty file_bytes" in res.message
    assert did_dispatch["called"] is False


@pytest.mark.asyncio
async def test_adapter_rejects_mismatched_file_bytes_sha256(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "https://ai.argus.local/extract-tender")
    did_dispatch = {"called": False}

    async def mock_post(self, url, headers=None, json=None):
        did_dispatch["called"] = True
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    mismatched_sha = "0" * 64
    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(
        tender_id="T1",
        document_id="doc1",
        document_sha256=mismatched_sha,
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "DOCUMENT_SHA256_MISMATCH"
    assert res.message == "Document content integrity verification failed."
    assert did_dispatch["called"] is False


@pytest.mark.asyncio
async def test_adapter_rejects_missing_bidder_id_for_document_extraction(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", "https://ai.argus.local/extract-doc")
    did_dispatch = {"called": False}

    async def mock_post(self, url, headers=None, json=None):
        did_dispatch["called"] = True
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    adapter = AIServiceAdapter()
    res = await adapter.extract_document(
        document_id="doc1",
        document_sha256=TEST_SHA256,
        bidder_id="",
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "AI_SERVICE_REQUEST_REJECTED"
    assert "Missing required bidder_id" in res.message
    assert did_dispatch["called"] is False


@pytest.mark.asyncio
async def test_adapter_rejects_legacy_data_field_in_v1_envelope(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", "https://ai.argus.local/extract-tender")

    async def mock_post_legacy(self, url, headers=None, json=None):
        return httpx.Response(
            200,
            json={
                "contract_version": "1.0",
                "request_id": json["request_id"],
                "document_id": json["document_id"],
                "document_sha256": json["document_sha256"],
                "status": "COMPLETED",
                "data": [{"clause": "1.1", "field": "general.gstin"}],  # Legacy fallback field
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_legacy)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender(
        tender_id="T1",
        document_id="doc1",
        document_sha256=TEST_SHA256,
        file_bytes=TEST_BYTES,
    )
    assert res.success is False
    assert res.error_code == "SCHEMA_VALIDATION_FAILED"
    assert "missing required 'requirements' list" in res.message

