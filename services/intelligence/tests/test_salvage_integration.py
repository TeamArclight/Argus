"""Comprehensive integration tests for ARGUS AI/RAG salvage and backend boundaries."""
import base64
import hashlib
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from argus_ai.contracts import (
    DocumentType,
    ExtractedFactDraft,
    Operator,
    RequirementType,
    TenderRequirementDraft,
)
from argus_ai.http_service import create_app
from argus_ai.model_gateway.gateway import ModelGateway, GroundedResponse
from argus_ai.rag.service import InMemoryRAG
from argus_ai.risk.context import detect_fact_risks
from argus_ai.storage import DocumentResolutionError, resolved_document


# ---------------------------------------------------------------------------
# 1. EXTRACTION CONTRACT MAPPING & ENVELOPE TESTS
# ---------------------------------------------------------------------------

def test_extract_tender_envelope_contract_mapping(tmp_path):
    """Verify tender extraction endpoint supports contract_version 1.0 envelope with base64 bytes."""
    app = create_app()
    client = TestClient(app)

    raw_text = b"Minimum average annual turnover shall be INR 5,00,00,000 for the last 3 financial years."
    doc_sha = hashlib.sha256(raw_text).hexdigest()
    b64_data = base64.b64encode(raw_text).decode("utf-8")

    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-12345",
        "tender_id": "tender-abc",
        "document_id": "doc-xyz",
        "document_sha256": doc_sha,
        "filename": "tender.txt",
        "content_type": "text/plain",
        "file_bytes_base64": b64_data,
    }

    resp = client.post("/extract-tender", json=req_payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["contract_version"] == "1.0"
    assert data["request_id"] == "req-12345"
    assert data["document_id"] == "doc-xyz"
    assert data["document_sha256"] == doc_sha
    assert data["status"] == "COMPLETED"
    assert "requirements" in data
    assert len(data["requirements"]) >= 1

    # Unapproved candidate preservation
    for req in data["requirements"]:
        assert req["is_approved"] is False
        assert "confidence" in req
        assert "metadata_json" in req


def test_extract_document_envelope_contract_mapping(tmp_path):
    """Verify document extraction endpoint supports contract_version 1.0 envelope with base64 bytes."""
    app = create_app()
    client = TestClient(app)

    raw_text = b"GSTIN: 07AABCA1234H1Z9\nPAN: AABCA1234H\nTurnover for FY 2024-25: INR 4,50,00,000"
    doc_sha = hashlib.sha256(raw_text).hexdigest()
    b64_data = base64.b64encode(raw_text).decode("utf-8")

    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-9999",
        "document_id": "doc-doc1",
        "bidder_id": "bidder-alpha",
        "document_type": "GST_CERT",
        "document_sha256": doc_sha,
        "filename": "gst.txt",
        "content_type": "text/plain",
        "file_bytes_base64": b64_data,
    }

    resp = client.post("/extract-document", json=req_payload)
    assert resp.status_code == 200
    data = resp.json()

    assert data["contract_version"] == "1.0"
    assert data["request_id"] == "req-9999"
    assert data["document_id"] == "doc-doc1"
    assert data["bidder_id"] == "bidder-alpha"
    assert data["document_sha256"] == doc_sha
    assert data["status"] == "COMPLETED"
    assert "facts" in data

    fields = {f["field"]: f["value"] for f in data["facts"]}
    assert "tax.gstin" in fields
    assert fields["tax.gstin"] == "07AABCA1234H1Z9"


def test_document_sha256_mismatch_rejected():
    """Verify payload with mismatched SHA-256 is rejected with HTTP 422."""
    app = create_app()
    client = TestClient(app)

    raw_text = b"Sample document bytes"
    b64_data = base64.b64encode(raw_text).decode("utf-8")

    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-fail",
        "document_id": "doc-fail",
        "bidder_id": "bidder-fail",
        "document_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "file_bytes_base64": b64_data,
    }

    resp = client.post("/extract-document", json=req_payload)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 2. RAG RETRIEVAL, SCOPE FILTERING, AND DELETION
# ---------------------------------------------------------------------------

def test_rag_scope_isolation_and_deletion(tmp_path):
    """Verify RAG filters properly by tender_id/metadata and deletes document chunks."""
    store = InMemoryRAG()
    app = create_app(rag=store)
    client = TestClient(app)

    # Ingest tender 1 policy
    p1 = tmp_path / "p1.txt"
    p1.write_text("Tender 1 requires ISO 9001 certification for quality assurance.")
    resp1 = client.post("/rag-ingest", json={
        "document_id": "DOC_T1",
        "title": "Tender 1 QA Policy",
        "document_uri": str(p1),
        "document_type": "POLICY",
        "tender_id": "TENDER_1",
    })
    assert resp1.status_code == 200

    # Ingest tender 2 policy
    p2 = tmp_path / "p2.txt"
    p2.write_text("Tender 2 requires ISO 27001 certification for information security.")
    resp2 = client.post("/rag-ingest", json={
        "document_id": "DOC_T2",
        "title": "Tender 2 InfoSec Policy",
        "document_uri": str(p2),
        "document_type": "POLICY",
        "tender_id": "TENDER_2",
    })
    assert resp2.status_code == 200

    # Query scoped to Tender 1
    q1 = client.post("/rag-query", json={"query": "ISO certification", "tender_id": "TENDER_1"})
    assert q1.status_code == 200
    res1 = q1.json()["results"]
    for r in res1:
        assert r["location_metadata"].get("tender_id") == "TENDER_1"

    # Delete Document 1
    del_resp = client.post("/rag-delete", json={"document_id": "DOC_T1"})
    assert del_resp.status_code == 200
    assert del_resp.json()["chunks_deleted"] >= 1

    # Query again for Tender 1 - should be empty
    q1_after = client.post("/rag-query", json={"query": "ISO certification", "tender_id": "TENDER_1"})
    assert q1_after.status_code == 200
    assert len(q1_after.json()["results"]) == 0


# ---------------------------------------------------------------------------
# 3. LIVE PROVIDER FAILURE BEHAVIOR & MOCK DEMO EXPLICITNESS
# ---------------------------------------------------------------------------

def test_live_provider_missing_api_key_raises(monkeypatch):
    """When ARGUS_MODEL_PROVIDER=gemini but ARGUS_GEMINI_API_KEY is missing, raise cleanly."""
    monkeypatch.setenv("ARGUS_MODEL_PROVIDER", "gemini")
    monkeypatch.delenv("ARGUS_GEMINI_API_KEY", raising=False)
    
    from argus_ai.model_gateway.gateway import configured_gateway
    with pytest.raises(RuntimeError, match="ARGUS_GEMINI_API_KEY is not configured"):
        configured_gateway()


def test_offline_mode_returns_unconfigured_gateway(monkeypatch):
    """When ARGUS_MODEL_PROVIDER=disabled, gateway is explicit about being unconfigured."""
    monkeypatch.setenv("ARGUS_MODEL_PROVIDER", "disabled")
    from argus_ai.model_gateway.gateway import configured_gateway
    gw = configured_gateway()
    assert gw.provider is None
    health = gw.health()
    assert health["configured"] is False


# ---------------------------------------------------------------------------
# 4. WORKFLOW COMPLIANCE BOUNDARY
# ---------------------------------------------------------------------------

def test_workflow_compliance_boundary(tmp_path):
    """Verify workflow delegates compliance evaluation and never fabricates qualification."""
    from argus_ai.agents.workflow import build_argus_workflow

    tender_file = tmp_path / "tender.txt"
    tender_file.write_text("Turnover: INR 1,00,00,000")
    bidder_file = tmp_path / "bidder.txt"
    bidder_file.write_text("Turnover: INR 2,00,00,000\nGSTIN: 07AABCA1234H1Z9")

    # When no backend compliance tool is injected, defaults to REVIEW_REQUIRED
    workflow = build_argus_workflow()
    state = {
        "tender": {"document_uri": str(tender_file)},
        "document": {
            "document_uri": str(bidder_file),
            "document_id": "d1",
            "bidder_id": "b1",
        },
        "rag_query": {"query": "turnover"},
    }

    result = workflow.invoke(state)
    assert result["compliance_evaluation"]["status"] == "REVIEW_REQUIRED"
    assert result["compliance_evaluation"]["source"] == "no_backend_compliance_engine"
