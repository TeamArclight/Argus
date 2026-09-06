import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import Base, engine, get_db
from app.main import app
from app.models.domain import Bidder, Document, ExtractedFact, Tender
from app.schemas.canonical import RAGQueryRequest
from app.services.ai_adapter import AIServiceAdapter
from app.services.rag_adapter import RAGServiceAdapter


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
    res = await adapter.extract_tender("T1", "s3://tenders/tender1.pdf")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert res.data is None


@pytest.mark.asyncio
async def test_base_url_alone_does_not_invent_extract_document(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", "https://ai.argus.local")
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", None)

    adapter = AIServiceAdapter()
    res = await adapter.extract_document("D1", "s3://bidders/doc1.pdf", "B1")
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
    res = await adapter.extract_tender("T1", "s3://tenders/t1.pdf")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_missing_explicit_document_url_returns_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", None)
    adapter = AIServiceAdapter()
    res = await adapter.extract_document("D1", "s3://docs/d1.pdf", "B1")
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
        (429, "AI_SERVICE_UNAVAILABLE", "RAG_SERVICE_UNAVAILABLE", True),
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
    ai_res = await ai_adapter.extract_tender("T1", "s3://tenders/t1.pdf")
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
        return httpx.Response(200, json={"facts": [{"value": "123"}]}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_missing_field)
    adapter = AIServiceAdapter()
    res1 = await adapter.extract_document("D1", "s3://doc.pdf", "B1")
    assert res1.success is False
    assert res1.error_code == "SCHEMA_VALIDATION_FAILED"

    # Case 2: confidence out of bounds (> 1.0)
    async def mock_post_bad_confidence(self, url, headers=None, json=None):
        req = httpx.Request("POST", url)
        return httpx.Response(200, json={"facts": [{"field": "gstin", "value": "27A", "confidence": 1.5}]}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post_bad_confidence)
    res2 = await adapter.extract_document("D1", "s3://doc.pdf", "B1")
    assert res2.success is False
    assert res2.error_code == "SCHEMA_VALIDATION_FAILED"


def test_tender_processing_without_raw_document_uri_fails():
    with TestClient(app) as client:
        t_payload = {
            "tender_number": "GEM/2026/NO_DOC/001",
            "title": "Tender Missing Raw Document URI",
            "raw_document_uri": None,
        }
        resp = client.post("/api/v1/tenders", json=t_payload)
        assert resp.status_code == 201
        tender_id = resp.json()["id"]

        proc_resp = client.post(f"/api/v1/tenders/{tender_id}/process")
        assert proc_resp.status_code == 200
        job = proc_resp.json()
        assert job["status"] == "FAILED"
        assert "Missing tender raw_document_uri" in job["error_message"]


def test_bidder_creation_creates_no_documents_or_extracted_facts():
    with TestClient(app) as client:
        t_payload = {
            "tender_number": "GEM/2026/BIDDER_TEST/001",
            "title": "Tender for Bidder Creation Test",
            "raw_document_uri": "s3://tenders/doc.pdf",
        }
        t_resp = client.post("/api/v1/tenders", json=t_payload).json()
        tender_id = t_resp["id"]

        b_payload = {
            "bidder_name": "Clean Entity Pvt Ltd",
            "gstin": "27AAAAA0000A1Z5",
        }
        b_resp = client.post(f"/api/v1/tenders/{tender_id}/bidders", json=b_payload)
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
    res = await adapter.extract_tender("T1", "s3://tenders/doc.pdf")
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
    res = await adapter.extract_tender("T1", "s3://tenders/doc.pdf")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert "transport failure" in res.message.lower()
