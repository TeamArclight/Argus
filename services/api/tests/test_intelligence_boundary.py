import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import Base, engine, get_db
from app.main import app
from app.models.domain import Bidder, Document, ExtractedFact, ProcessingJob, Tender
from app.schemas.canonical import RAGQueryRequest
from app.services.ai_adapter import AIServiceAdapter
from app.services.rag_adapter import RAGServiceAdapter


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.mark.asyncio
async def test_ai_adapter_without_config_returns_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", None)
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL", None)
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL", None)

    adapter = AIServiceAdapter()
    t_res = await adapter.extract_tender("T1", "s3://tenders/tender1.pdf")
    assert t_res.success is False
    assert t_res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert t_res.data is None

    d_res = await adapter.extract_document("DOC1", "s3://bidders/doc1.pdf", "B1")
    assert d_res.success is False
    assert d_res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert d_res.data is None


@pytest.mark.asyncio
async def test_no_hardcoded_tender_requirements_or_bidder_facts(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", None)
    adapter = AIServiceAdapter()

    t_res = await adapter.extract_tender("T1", "s3://tenders/tender1.pdf")
    assert t_res.data is None

    d_res = await adapter.extract_document("D1", "s3://docs/d1.pdf", "B1")
    assert d_res.data is None


def test_tender_processing_without_raw_document_uri_fails():
    with TestClient(app) as client:
        # Create tender without raw_document_uri
        t_payload = {
            "tender_number": "GEM/2026/NO_DOC/001",
            "title": "Tender Missing Raw Document URI",
            "raw_document_uri": None,
        }
        resp = client.post("/api/v1/tenders", json=t_payload)
        assert resp.status_code == 201
        tender_id = resp.json()["id"]

        # Trigger processing
        proc_resp = client.post(f"/api/v1/tenders/{tender_id}/process")
        assert proc_resp.status_code == 200
        job = proc_resp.json()
        assert job["status"] == "FAILED"
        assert "Missing tender raw_document_uri" in job["error_message"]

        # Check tender status set to FAILED
        t_resp = client.get(f"/api/v1/tenders/{tender_id}")
        assert t_resp.json()["status"] == "FAILED"


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

        # Verify DB directly: 0 Document and 0 ExtractedFact records created
        db: Session = next(get_db())
        doc_count = db.query(Document).filter(Document.bidder_id == bidder_id).count()
        fact_count = db.query(ExtractedFact).filter(ExtractedFact.bidder_id == bidder_id).count()
        db.close()

        assert doc_count == 0
        assert fact_count == 0


@pytest.mark.asyncio
async def test_ai_timeout_returns_structured_failure(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", "https://ai.argus.local")

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
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", "https://ai.argus.local")

    async def mock_post(self, url, headers=None, json=None):
        raise httpx.RequestError("Connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender("T1", "s3://tenders/doc.pdf")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert "transport failure" in res.message.lower()


@pytest.mark.asyncio
async def test_malformed_json_returns_structured_failure(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", "https://ai.argus.local")

    async def mock_post(self, url, headers=None, json=None):
        req = httpx.Request("POST", url)
        return httpx.Response(200, text="NOT_VALID_JSON", request=req)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender("T1", "s3://tenders/doc.pdf")
    assert res.success is False
    assert res.error_code == "AI_SERVICE_UNAVAILABLE"
    assert "failed to parse" in res.message.lower()


@pytest.mark.asyncio
async def test_invalid_schema_returns_schema_validation_failed(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", "https://ai.argus.local")

    async def mock_post(self, url, headers=None, json=None):
        req = httpx.Request("POST", url)
        # Invalid requirement payload (missing mandatory clause/requirement_type/operator)
        return httpx.Response(200, json={"requirements": [{"invalid_key": "val"}]}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    adapter = AIServiceAdapter()
    res = await adapter.extract_tender("T1", "s3://tenders/doc.pdf")
    assert res.success is False
    assert res.error_code == "SCHEMA_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_rag_without_config_returns_unavailable(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", None)
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_RAG_URL", None)

    adapter = RAGServiceAdapter()
    res = await adapter.retrieve(RAGQueryRequest(query="GFR Rule 144 turnover exemption"))
    assert res.results == []
    assert res.error_code == "RAG_SERVICE_UNAVAILABLE"
    assert "unconfigured or unavailable" in res.error_message


@pytest.mark.asyncio
async def test_no_hardcoded_rag_evidence(monkeypatch):
    monkeypatch.setattr(settings, "ARGUS_INTELLIGENCE_BASE_URL", None)
    adapter = RAGServiceAdapter()

    res = await adapter.retrieve(RAGQueryRequest(query="Minimum turnover criteria"))
    # Check no hardcoded GFR or MSE evidence is fabricated
    assert len(res.results) == 0
