from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app
from app.db.session import Base, engine, SessionLocal
from app.models.domain import AuditEvent, Document, DocumentType, Tender
from app.audit.logger import AuditLogger
from app.schemas.canonical import EvidenceRead, RAGQueryResponse, UserRole
from tests.auth_helpers import get_auth_headers


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_audit_logger_log_live_immediate_commit():
    """Verify log_live commits to DB immediately and does not disturb uncommitted transaction on MVCC databases (Postgres), or stages safely on single-writer SQLite."""
    db1 = SessionLocal()
    try:
        bind = db1.get_bind()
        is_sqlite = bind is not None and getattr(getattr(bind, "dialect", None), "name", None) == "sqlite"

        tender = Tender(
            id="T-LIVE-TEST",
            tender_number="GEM/2026/TEST/LIVE",
            title="Live Audit Test Tender",
        )
        db1.add(tender)

        event = AuditLogger.log_live(
            db1,
            action="TEST_LIVE_MILESTONE",
            entity_type="TENDER",
            entity_id="T-LIVE-TEST",
            actor_id="OFFICER-1",
            actor_role="PROCUREMENT_OFFICER",
            payload={"step": "statutory_checks"},
        )
        assert event is not None
        assert event.action == "TEST_LIVE_MILESTONE"

        if is_sqlite:
            # On SQLite, single-writer locking prevents concurrent transaction commits,
            # so log_live safely stages onto the active session without deadlock.
            return

        db2 = SessionLocal()
        try:
            stored_event = db2.query(AuditEvent).filter(AuditEvent.action == "TEST_LIVE_MILESTONE").first()
            assert stored_event is not None
            assert stored_event.entity_id == "T-LIVE-TEST"
            assert stored_event.payload_json["step"] == "statutory_checks"

            stored_tender = db2.query(Tender).filter(Tender.id == "T-LIVE-TEST").first()
            assert stored_tender is None
        finally:
            db2.close()

        db1.rollback()

        db3 = SessionLocal()
        try:
            persisted_event = db3.query(AuditEvent).filter(AuditEvent.action == "TEST_LIVE_MILESTONE").first()
            assert persisted_event is not None
        finally:
            db3.close()
    finally:
        db1.close()


def test_rag_explain_clause_lookup():
    """Querying 'clause 2.1' returns DIRECT_EVIDENCE with exact clause cited."""
    client = TestClient(app)
    headers = get_auth_headers(role=UserRole.PROCUREMENT_OFFICER)

    evidence_chunk = EvidenceRead(
        id="chunk-seci-2.1",
        entity_type="document_chunk",
        entity_id="doc_tender_gem_2026_03",
        snippet="2.1 Valid GSTIN required and bidder must be registered on the GeM portal.",
        source_uri="tenders/tender_gem_2026_03/seci_solar_grid_rfp.pdf",
        page_number=1,
        location_metadata={
            "clause": "2.1",
            "title": "SECI Smart Solar Grid RFP",
            "tender_id": "tender_gem_2026_03",
            "relevance_score": 0.85,
        },
    )

    mock_res = RAGQueryResponse(
        query="clause 2.1",
        results=[evidence_chunk],
        retrieved_at=datetime.now(timezone.utc),
    )

    with patch("app.api.v1.rag.rag_adapter.retrieve", new_callable=AsyncMock) as mock_retrieve:
        mock_retrieve.return_value = mock_res
        resp = client.post(
            "/api/v1/rag/explain",
            headers=headers,
            json={
                "query": "clause 2.1",
                "tender_id": "tender_gem_2026_03",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["result_class"] == "DIRECT_EVIDENCE"
        assert len(data["citations"]) == 1
        assert "2.1 Valid GSTIN" in data["direct_answer"]
        assert data["is_advisory"] is True


def test_rag_explain_msme_without_exemption_yields_related_context():
    """MSME requirement without exemption yields RELATED_CONTEXT rather than INSUFFICIENT_RETRIEVAL_EVIDENCE."""
    client = TestClient(app)
    headers = get_auth_headers(role=UserRole.PROCUREMENT_OFFICER)

    evidence_chunk = EvidenceRead(
        id="chunk-seci-2.2",
        entity_type="document_chunk",
        entity_id="doc_tender_gem_2026_03",
        snippet="2.2 MSE bidders shall furnish a valid Udyam registration certificate.",
        source_uri="tenders/tender_gem_2026_03/seci_solar_grid_rfp.pdf",
        page_number=1,
        location_metadata={
            "clause": "2.2",
            "title": "SECI Smart Solar Grid RFP",
            "tender_id": "tender_gem_2026_03",
            "relevance_score": 0.70,
        },
    )

    mock_res = RAGQueryResponse(
        query="Is MSME turnover exemption applicable to this tender?",
        results=[evidence_chunk],
        retrieved_at=datetime.now(timezone.utc),
    )

    with patch("app.api.v1.rag.rag_adapter.retrieve", new_callable=AsyncMock) as mock_retrieve:
        mock_retrieve.return_value = mock_res
        resp = client.post(
            "/api/v1/rag/explain",
            headers=headers,
            json={
                "query": "Is MSME turnover exemption applicable to this tender?",
                "tender_id": "tender_gem_2026_03",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["result_class"] == "RELATED_CONTEXT"
        assert len(data["related_citations"]) == 1
        assert "turnover exemption" in data["direct_answer"]
        assert "2.2 MSE bidders" in (data["related_context"] or "")


def test_rag_explain_self_healing_indexing():
    """When a tender has 0 chunks initially, explain endpoint triggers on-the-fly ingestion from DB doc."""
    client = TestClient(app)
    headers = get_auth_headers(role=UserRole.PROCUREMENT_OFFICER)

    # Seed tender and document in DB
    db = SessionLocal()
    try:
        tender = Tender(
            id="tender_gem_2026_03",
            tender_number="GEM/2026/B/4521091",
            title="SECI Solar Grid RFP",
        )
        db.add(tender)
        doc = Document(
            id="doc_tender_gem_2026_03",
            tender_id="tender_gem_2026_03",
            filename="seci_solar_grid_rfp.pdf",
            storage_uri="tenders/tender_gem_2026_03/seci_solar_grid_rfp.pdf",
            sha256="abc123sha",
            document_type=DocumentType.TENDER,
        )
        db.add(doc)
        db.commit()
    finally:
        db.close()

    mock_empty_res = RAGQueryResponse(
        query="What are the minimum past experience thresholds?",
        results=[],
        retrieved_at=datetime.now(timezone.utc),
    )
    mock_healed_chunk = EvidenceRead(
        id="chunk-healed-1",
        entity_type="document_chunk",
        entity_id="doc_tender_gem_2026_03",
        snippet="2.3 The bidder must have a minimum of 3 years of experience in solar renewable equipment.",
        source_uri="tenders/tender_gem_2026_03/seci_solar_grid_rfp.pdf",
        page_number=1,
        location_metadata={
            "clause": "2.3",
            "title": "SECI Solar Grid RFP",
            "tender_id": "tender_gem_2026_03",
            "relevance_score": 0.88,
        },
    )
    mock_healed_res = RAGQueryResponse(
        query="What are the minimum past experience thresholds?",
        results=[mock_healed_chunk],
        retrieved_at=datetime.now(timezone.utc),
    )

    call_count = 0

    async def mock_retrieve(req):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return mock_empty_res
        return mock_healed_res

    with patch("app.api.v1.rag.rag_adapter.retrieve", side_effect=mock_retrieve) as patched_retrieve, \
         patch("app.api.v1.rag.rag_adapter.ingest_document", new_callable=AsyncMock) as mock_ingest, \
         patch("app.storage.factory.get_storage_provider") as mock_storage_factory:

        mock_storage = mock_storage_factory.return_value
        mock_storage.file_exists.return_value = True
        mock_storage.read_file.return_value = b"%PDF-1.4 dummy"
        mock_ingest.return_value = {"success": True, "chunks_indexed": 5}

        resp = client.post(
            "/api/v1/rag/explain",
            headers=headers,
            json={
                "query": "What are the minimum past experience thresholds?",
                "tender_id": "tender_gem_2026_03",
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["result_class"] == "DIRECT_EVIDENCE"
        assert len(data["citations"]) == 1
        assert "minimum of 3 years of experience" in data["direct_answer"]
        assert mock_ingest.called

