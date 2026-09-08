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


# ---------------------------------------------------------------------------
# 5. PGVECTOR RAG SCHEMA & VECTOR FORMATTING
# ---------------------------------------------------------------------------

def test_pgvector_rag_schema_and_vector_formatting():
    """Verify PgVectorRAG compiles valid DDL and formats vectors correctly."""
    from argus_ai.rag.pgvector import PgVectorRAG, _vector
    from unittest.mock import MagicMock

    # Vector formatting helper
    vec = [0.1, 0.25, -0.5, 0.0]
    assert _vector(vec) == "[0.1,0.25,-0.5,0.0]"

    # Schema compilation check with mock connection
    rag = PgVectorRAG(database_url="postgresql://user:pass@localhost:5432/argus")
    mock_cursor = MagicMock()
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    with patch.object(rag, "_connect", return_value=mock_conn):
        rag.ensure_schema()

    # Verify executed SQLs
    calls = [call[0][0] for call in mock_cursor.execute.call_args_list]
    assert any("CREATE EXTENSION IF NOT EXISTS vector" in sql for sql in calls)
    assert any("CREATE TABLE IF NOT EXISTS intelligence_evidence_chunks" in sql for sql in calls)
    assert any("CREATE INDEX IF NOT EXISTS intelligence_chunks_embedding_idx" in sql for sql in calls)
    assert any("CREATE INDEX IF NOT EXISTS intelligence_chunks_fts_idx" in sql for sql in calls)


# ---------------------------------------------------------------------------
# 6. OCR ERROR HANDLING ON MISSING BINARIES / DEPENDENCIES
# ---------------------------------------------------------------------------

def test_ocr_missing_binaries_graceful_handling(tmp_path):
    """Verify image/scanned PDF parsing raises DocumentParseError when OCR fails or is missing."""
    from argus_ai.parsing.service import parse_document, DocumentParseError

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82")

    # On host without Pillow/pytesseract or when OCR fails, must raise DocumentParseError cleanly
    with pytest.raises(DocumentParseError):
        parse_document(img_file)


# ---------------------------------------------------------------------------
# 7. SYNTHETIC END-TO-END FLOW REPLAY
# ---------------------------------------------------------------------------

def test_synthetic_end_to_end_replay(tmp_path):
    """Replay synthetic tender extract -> approve -> bidder fact extract -> compliance check -> risk detection."""
    from argus_ai.extraction.service import extract_tender, extract_document
    from argus_ai.model_gateway.gateway import ModelGateway
    from argus_ai.risk.service import detect_risk

    # 1. Tender extraction
    tender_doc = tmp_path / "synthetic_tender.txt"
    tender_doc.write_text(
        "TENDER NOTICE\n"
        "1. Bidder must have minimum average annual turnover of INR 3,00,00,000.\n"
        "2. Bidder must hold valid GSTIN registration in Delhi.\n"
        "3. Bidder must have minimum 3 years of experience in IT infrastructure.\n"
    )

    gw = ModelGateway()
    candidate_reqs = extract_tender(tender_doc, gw)
    assert len(candidate_reqs) >= 1

    # 2. Approved requirements (in API domain)
    # Extracted candidate requirements are unapproved drafts until authorized by tender authority
    for req in candidate_reqs:
        assert req.clause is not None
        assert req.confidence > 0.0

    # 3. Bidder document extraction
    bidder_doc = tmp_path / "synthetic_bidder.txt"
    bidder_doc.write_text(
        "BIDDER PROFILE\n"
        "GSTIN: 07AABCA1234H1Z9\n"
        "PAN: AABCA1234H\n"
        "Average annual turnover: INR 5,00,00,000\n"
        "Experience in IT infrastructure: 5 years\n"
    )

    facts = extract_document(bidder_doc, document_id="doc_bidder_1", bidder_id="bidder_1", gateway=gw)
    assert len(facts) >= 1
    fact_dict = {f.field: f.value for f in facts}
    assert "tax.gstin" in fact_dict
    assert fact_dict["tax.gstin"] == "07AABCA1234H1Z9"

    # 4. Risk detection
    risk_signals = detect_risk(
        document_bytes=bidder_doc.read_bytes(),
        prior_hashes=set(),
        claim_value="07AABCA1234H1Z9",
        verified_value="07AABCA1234H1Z9",
        claimed_entity_name="Acme Corp",
        verified_entity_name="Acme Corp",
    )
    # Valid matching claim and verified value yields 0 mismatch risk signals
    assert not any(s.signal_type == "CLAIM_VERIFIED_MISMATCH" for s in risk_signals)


# ---------------------------------------------------------------------------
# 8. HTTP SECURITY, AUTH ENFORCEMENT & BOUNDED PAYLOADS
# ---------------------------------------------------------------------------

def test_require_auth_enforced_when_configured(monkeypatch):
    """When ARGUS_REQUIRE_AUTH=true, reject unauthenticated requests."""
    monkeypatch.setenv("ARGUS_REQUIRE_AUTH", "true")
    monkeypatch.setenv("ARGUS_INTELLIGENCE_API_KEY", "test-secret-key")
    app = create_app()
    client = TestClient(app)

    # Missing auth header -> 401
    resp = client.get("/health")
    assert resp.status_code == 401

    # Wrong token -> 401
    resp_wrong = client.get("/health", headers={"Authorization": "Bearer wrong-key"})
    assert resp_wrong.status_code == 401

    # Correct token -> 200
    resp_ok = client.get("/health", headers={"Authorization": "Bearer test-secret-key"})
    assert resp_ok.status_code == 200


def test_oversized_base64_payload_rejected():
    """Verify base64 payload exceeding size limits is rejected with HTTP 413 before decoding."""
    app = create_app()
    client = TestClient(app)

    huge_b64 = "A" * (45 * 1024 * 1024)  # 45MB string exceeds max bounded limit

    req_payload = {
        "contract_version": "1.0",
        "tender_id": "t1",
        "file_bytes_base64": huge_b64,
    }
    resp = client.post("/extract-tender", json=req_payload)
    assert resp.status_code == 413


def test_rag_query_rejects_invalid_filter_key():
    """Verify RAG query rejects SQL injection or invalid characters in filter keys."""
    app = create_app()
    client = TestClient(app)

    resp = client.post("/rag-query", json={
        "query": "turnover",
        "filters": {"invalid-key' OR '1'='1": "value"}
    })
    assert resp.status_code == 422


def test_rag_delete_rejects_malformed_document_id():
    """Verify RAG delete rejects invalid document_id patterns."""
    app = create_app()
    client = TestClient(app)

    resp = client.post("/rag-delete", json={"document_id": ""})
    assert resp.status_code == 422

    resp2 = client.post("/rag-delete", json={"document_id": "doc/../../evil"})
    assert resp2.status_code == 422


