import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

import app.models.domain  # noqa: F401
from app.db.session import Base, engine, SessionLocal
from app.main import app
from app.models.domain import AuditEvent, Bidder, Document, ProcessingJob, Tender, TenderRequirement
from app.schemas.canonical import (
    DocumentType,
    JobStage,
    JobStatus,
    OperatorEnum,
    RequirementType,
    UserRole,
)
from tests.auth_helpers import get_auth_headers


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_unauthenticated_delete_returns_401():
    with SessionLocal() as db:
        tender = Tender(
            id="t_del_auth",
            tender_number="T-DEL-AUTH",
            title="Test Auth Tender",
            status=JobStatus.FAILED,
        )
        db.add(tender)
        db.commit()

    with TestClient(app) as client:
        resp = client.delete("/api/v1/tenders/t_del_auth/documents/doc_any")
        assert resp.status_code == 401


def test_wrong_tender_document_relationship_rejected():
    with SessionLocal() as db:
        tender1 = Tender(id="t_del_rel1", tender_number="T-DEL-REL1", title="T1", status=JobStatus.FAILED)
        tender2 = Tender(id="t_del_rel2", tender_number="T-DEL-REL2", title="T2", status=JobStatus.FAILED)
        doc = Document(
            id="doc_rel2",
            tender_id="t_del_rel2",
            document_type=DocumentType.TENDER,
            filename="rfp.pdf",
            storage_uri="tenders/t_del_rel2/doc_rel2.pdf",
            sha256="abc",
        )
        db.add_all([tender1, tender2, doc])
        db.commit()

    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        resp = client.delete("/api/v1/tenders/t_del_rel1/documents/doc_rel2", headers=headers)
        assert resp.status_code == 400
        msg = resp.json()["error"]["message"]
        assert "does not belong to tender" in msg


def test_bidder_document_rejected():
    with SessionLocal() as db:
        tender = Tender(id="t_del_bid", tender_number="T-DEL-BID", title="T Bid", status=JobStatus.FAILED)
        bidder = Bidder(id="b_del_1", tender_id="t_del_bid", bidder_name="Bidder 1")
        doc = Document(
            id="doc_bidder_1",
            tender_id=None,
            bidder_id="b_del_1",
            document_type=DocumentType.FINANCIAL_STATEMENT,
            filename="fin.pdf",
            storage_uri="bidders/b_del_1/fin.pdf",
            sha256="abc",
        )
        db.add_all([tender, bidder, doc])
        db.commit()

    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        resp = client.delete("/api/v1/tenders/t_del_bid/documents/doc_bidder_1", headers=headers)
        assert resp.status_code == 400
        msg = resp.json()["error"]["message"]
        assert "bidder document" in msg.lower() or "does not belong to tender" in msg.lower()


def test_cannot_delete_when_not_failed():
    with SessionLocal() as db:
        tender = Tender(id="t_del_active", tender_number="T-DEL-ACTIVE", title="Active", status=JobStatus.RUNNING)
        doc = Document(
            id="doc_active",
            tender_id="t_del_active",
            document_type=DocumentType.TENDER,
            filename="rfp.pdf",
            storage_uri="tenders/t_del_active/doc.pdf",
            sha256="abc",
        )
        db.add_all([tender, doc])
        db.commit()

    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        resp = client.delete("/api/v1/tenders/t_del_active/documents/doc_active", headers=headers)
        assert resp.status_code == 400
        msg = resp.json()["error"]["message"]
        assert "only permitted when tender processing or requirement extraction has failed" in msg


def test_approved_requirement_blocks_deletion_with_409():
    with SessionLocal() as db:
        tender = Tender(id="t_del_appr", tender_number="T-DEL-APPR", title="Appr", status=JobStatus.FAILED)
        doc = Document(
            id="doc_appr",
            tender_id="t_del_appr",
            document_type=DocumentType.TENDER,
            filename="rfp.pdf",
            storage_uri="tenders/t_del_appr/doc.pdf",
            sha256="abc",
        )
        req = TenderRequirement(
            tender_id="t_del_appr",
            clause="2.1",
            requirement_type=RequirementType.GST,
            field="tax.gstin",
            operator=OperatorEnum.EXISTS,
            expected_value=True,
            confidence=0.9,
            is_approved=True,
            document_id="doc_appr",
        )
        db.add_all([tender, doc, req])
        db.commit()

    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        resp = client.delete("/api/v1/tenders/t_del_appr/documents/doc_appr", headers=headers)
        assert resp.status_code == 409
        msg = resp.json()["error"]["message"]
        assert "Document cannot be deleted because approved tender requirements depend on it." in msg


def test_storage_failure_fails_safely_500(monkeypatch):
    with SessionLocal() as db:
        tender = Tender(id="t_del_stor", tender_number="T-DEL-STOR", title="Stor Fail", status=JobStatus.FAILED)
        doc = Document(
            id="doc_stor",
            tender_id="t_del_stor",
            document_type=DocumentType.TENDER,
            filename="rfp.pdf",
            storage_uri="tenders/t_del_stor/doc.pdf",
            sha256="abc",
        )
        db.add_all([tender, doc])
        db.commit()

    from app.api.v1 import tenders as tenders_module
    mock_provider = MagicMock()
    mock_provider.delete_file.side_effect = RuntimeError("Storage connection dropped")
    monkeypatch.setattr(tenders_module, "get_storage_provider", lambda: mock_provider)

    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        resp = client.delete("/api/v1/tenders/t_del_stor/documents/doc_stor", headers=headers)
        assert resp.status_code == 500
        msg = resp.json()["error"]["message"]
        assert "Failed to delete physical document from storage" in msg

    with SessionLocal() as db:
        persisted_doc = db.query(Document).filter(Document.id == "doc_stor").first()
        assert persisted_doc is not None


def test_successful_delete_unapproved_purged_jobs_preserved_audit_logged(monkeypatch):
    with SessionLocal() as db:
        tender = Tender(
            id="t_del_success",
            tender_number="T-DEL-SUCCESS",
            title="Success Tender",
            status=JobStatus.FAILED,
            raw_document_uri="tenders/t_del_success/rfp.pdf",
        )
        doc = Document(
            id="doc_success",
            tender_id="t_del_success",
            document_type=DocumentType.TENDER,
            filename="rfp.pdf",
            storage_uri="tenders/t_del_success/rfp.pdf",
            sha256="abc",
        )
        unapproved_req = TenderRequirement(
            tender_id="t_del_success",
            clause="1.1",
            requirement_type=RequirementType.TURNOVER,
            field="financial.average_annual_turnover",
            operator=OperatorEnum.GTE,
            expected_value=1000000,
            confidence=0.7,
            is_approved=False,
            document_id="doc_success",
        )
        failed_job = ProcessingJob(
            id="job_failed_extract_999",
            target_type="TENDER",
            target_id="t_del_success",
            job_type="EXTRACT_REQUIREMENTS",
            status=JobStatus.FAILED,
            current_stage=JobStage.EXTRACTION,
            progress=100,
            error_message="Intelligence service unavailable.",
        )
        db.add_all([tender, doc, unapproved_req, failed_job])
        db.commit()

    from app.api.v1 import tenders as tenders_module
    mock_provider = MagicMock()
    mock_provider.delete_file.return_value = True
    monkeypatch.setattr(tenders_module, "get_storage_provider", lambda: mock_provider)

    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        resp = client.delete("/api/v1/tenders/t_del_success/documents/doc_success", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    with SessionLocal() as db:
        # 1. Document record deleted from DB
        assert db.query(Document).filter(Document.id == "doc_success").first() is None

        # 2. Unapproved requirement purged
        assert db.query(TenderRequirement).filter(TenderRequirement.tender_id == "t_del_success").count() == 0

        # 3. Tender state reset
        refreshed_tender = db.query(Tender).filter(Tender.id == "t_del_success").first()
        assert refreshed_tender.status == JobStatus.QUEUED
        assert refreshed_tender.raw_document_uri is None

        # 4. Historical failed ProcessingJob preserved
        assert db.query(ProcessingJob).filter(ProcessingJob.id == "job_failed_extract_999").first() is not None

        # 5. AuditEvent recorded with actor identity
        audit_evt = (
            db.query(AuditEvent)
            .filter(AuditEvent.action == "TENDER_DOCUMENT_DELETED", AuditEvent.entity_id == "doc_success")
            .first()
        )
        assert audit_evt is not None
        assert audit_evt.actor_role == "PROCUREMENT_OFFICER"
        payload = audit_evt.payload_json
        assert payload.get("previous_failed_job_id") == "job_failed_extract_999"
        assert payload.get("filename") == "rfp.pdf"


def test_deletion_preserves_unrelated_manual_requirements_with_null_document_id(monkeypatch):
    with SessionLocal() as db:
        tender = Tender(
            id="t_del_null_doc",
            tender_number="T-DEL-NULL-DOC",
            title="Null Doc Tender",
            status=JobStatus.FAILED,
            raw_document_uri="tenders/t_del_null_doc/rfp.pdf",
        )
        doc = Document(
            id="doc_to_delete",
            tender_id="t_del_null_doc",
            document_type=DocumentType.TENDER,
            filename="rfp.pdf",
            storage_uri="tenders/t_del_null_doc/rfp.pdf",
            sha256="abc",
        )
        doc_req = TenderRequirement(
            id="req_from_doc",
            tender_id="t_del_null_doc",
            clause="1.1",
            requirement_type=RequirementType.TURNOVER,
            field="financial.average_annual_turnover",
            operator=OperatorEnum.GTE,
            expected_value=1000000,
            confidence=0.7,
            is_approved=False,
            document_id="doc_to_delete",
        )
        manual_req = TenderRequirement(
            id="req_manual_unrelated",
            tender_id="t_del_null_doc",
            clause="2.0",
            requirement_type=RequirementType.GST,
            field="tax.gstin",
            operator=OperatorEnum.EXISTS,
            expected_value=True,
            confidence=1.0,
            is_approved=False,
            document_id=None,
        )
        db.add_all([tender, doc, doc_req, manual_req])
        db.commit()

    from app.api.v1 import tenders as tenders_module
    mock_provider = MagicMock()
    mock_provider.delete_file.return_value = True
    monkeypatch.setattr(tenders_module, "get_storage_provider", lambda: mock_provider)

    headers = get_auth_headers(UserRole.PROCUREMENT_OFFICER)
    with TestClient(app) as client:
        resp = client.delete("/api/v1/tenders/t_del_null_doc/documents/doc_to_delete", headers=headers)
        assert resp.status_code == 200

    with SessionLocal() as db:
        # Document record deleted
        assert db.query(Document).filter(Document.id == "doc_to_delete").first() is None
        # Document-linked requirement deleted
        assert db.query(TenderRequirement).filter(TenderRequirement.id == "req_from_doc").first() is None
        # Unrelated manual requirement with NULL document_id is preserved!
        preserved_manual = db.query(TenderRequirement).filter(TenderRequirement.id == "req_manual_unrelated").first()
        assert preserved_manual is not None
        assert preserved_manual.document_id is None

