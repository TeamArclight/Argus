import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.orm import Session
from app.db.session import Base, SessionLocal, engine
from app.models.domain import (
    Bidder,
    ComplianceRun,
    Document,
    DocumentType,
    Evidence,
    ExtractedFact,
    HumanDecision,
    HumanDecisionStatus,
    JobStatus,
    RuleEvaluation,
    Tender,
    TenderRequirement,
    VerificationResult,
)
from app.schemas.canonical import (
    ComplianceStatus,
    OperatorEnum,
    RequirementType,
    VerificationMode,
    VerificationStatus,
)
from app.services.bid_verification_service import BidVerificationService
from app.services.evidence_service import EvidenceNormalizationService, EvidenceOwnershipError
from app.api.v1.bidders import build_compliance_matrix


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def sample_tender(db_session: Session):
    tender = Tender(
        id=str(uuid.uuid4()),
        tender_number="TNT-TEST-001",
        title="Test Tender",
        status=JobStatus.COMPLETED,
    )
    db_session.add(tender)
    db_session.commit()
    db_session.refresh(tender)
    return tender


@pytest.fixture
def sample_bidder(db_session: Session, sample_tender: Tender):
    bidder = Bidder(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        bidder_name="Acme Corp Ltd",
        gstin="27AAACA12341ZV",
        status=HumanDecisionStatus.PENDING,
    )
    db_session.add(bidder)
    db_session.commit()
    db_session.refresh(bidder)
    return bidder


@pytest.mark.asyncio
async def test_historical_rule_mutation(db_session, sample_tender, sample_bidder):
    """1. Verifies compliance matrix reconstructed from run snapshot is unaffected by subsequent TenderRequirement mutation."""
    req = TenderRequirement(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        clause="3.1",
        requirement_type=RequirementType.TURNOVER,
        field="financial.average_annual_turnover",
        operator=OperatorEnum.GTE,
        expected_value="5000000",
        mandatory=True,
        is_approved=True,
    )
    db_session.add(req)

    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
        sha256="abc123sha4567890abc123sha45678901234567890abc123sha456789012345678",
    )
    db_session.add(doc)
    db_session.commit()

    fact = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="6000000",
        source_page=1,
        source_text="Turnover: 60 Lakhs",
        confidence=0.98,
    )
    db_session.add(fact)
    db_session.commit()

    service = BidVerificationService(db_session)
    await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    run = db_session.query(ComplianceRun).filter(ComplianceRun.bidder_id == sample_bidder.id).first()
    evals = db_session.query(RuleEvaluation).filter(RuleEvaluation.run_id == run.id).all()
    vers = db_session.query(VerificationResult).filter(VerificationResult.run_id == run.id).all()

    matrix_before, _, _ = build_compliance_matrix(db_session, sample_bidder, run, evals, vers)
    assert matrix_before.rows[0].expected_value == "5000000"

    # Mutate TenderRequirement threshold
    req.expected_value = "999999999"
    db_session.commit()

    matrix_after, _, _ = build_compliance_matrix(db_session, sample_bidder, run, evals, vers)
    assert matrix_after.rows[0].expected_value == "5000000"


@pytest.mark.asyncio
async def test_historical_fact_mutation(db_session, sample_tender, sample_bidder):
    """2. Verifies compliance matrix reconstructed from run snapshot is unaffected by subsequent ExtractedFact mutation."""
    req = TenderRequirement(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        clause="3.1",
        requirement_type=RequirementType.TURNOVER,
        field="financial.average_annual_turnover",
        operator=OperatorEnum.GTE,
        expected_value="5000000",
        mandatory=True,
        is_approved=True,
    )
    db_session.add(req)

    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
        sha256="abc123sha4567890abc123sha45678901234567890abc123sha456789012345678",
    )
    db_session.add(doc)
    db_session.commit()

    fact = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="6000000",
        source_page=1,
        source_text="Turnover: 60 Lakhs",
        confidence=0.98,
    )
    db_session.add(fact)
    db_session.commit()

    service = BidVerificationService(db_session)
    await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    run = db_session.query(ComplianceRun).filter(ComplianceRun.bidder_id == sample_bidder.id).first()
    evals = db_session.query(RuleEvaluation).filter(RuleEvaluation.run_id == run.id).all()
    vers = db_session.query(VerificationResult).filter(VerificationResult.run_id == run.id).all()

    # Mutate ExtractedFact value in DB
    fact.value = "100"
    db_session.commit()

    matrix, _, _ = build_compliance_matrix(db_session, sample_bidder, run, evals, vers)
    assert matrix.rows[0].observed_value == "6000000"
    assert matrix.rows[0].status == ComplianceStatus.PASS


@pytest.mark.asyncio
async def test_historical_verification_mutation(db_session, sample_tender, sample_bidder):
    """3. Verifies compliance matrix reconstructed from run snapshot is unaffected by subsequent VerificationResult mutation."""
    req = TenderRequirement(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        clause="3.1",
        requirement_type=RequirementType.TURNOVER,
        field="financial.average_annual_turnover",
        operator=OperatorEnum.GTE,
        expected_value="5000000",
        mandatory=True,
        is_approved=True,
    )
    db_session.add(req)
    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
    )
    db_session.add(doc)
    db_session.commit()

    fact = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="6000000",
    )
    db_session.add(fact)
    db_session.commit()

    service = BidVerificationService(db_session)
    await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    run = db_session.query(ComplianceRun).filter(ComplianceRun.bidder_id == sample_bidder.id).first()
    evals = db_session.query(RuleEvaluation).filter(RuleEvaluation.run_id == run.id).all()
    vers = db_session.query(VerificationResult).filter(VerificationResult.run_id == run.id).all()

    # Mutate VerificationResult in DB
    for v in vers:
        v.status = VerificationStatus.MISMATCH
        v.verified_value = "INVALID_GST"
    db_session.commit()

    matrix, _, _ = build_compliance_matrix(db_session, sample_bidder, run, evals, vers)
    assert matrix.rows[0].status == ComplianceStatus.PASS


@pytest.mark.asyncio
async def test_historical_evidence_mutation(db_session, sample_tender, sample_bidder):
    """4. Verifies compliance matrix reconstructed from run snapshot is unaffected by deletion or mutation of Evidence DB rows."""
    req = TenderRequirement(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        clause="1.1",
        requirement_type=RequirementType.GST,
        field="general.gstin",
        operator=OperatorEnum.EXISTS,
        expected_value="true",
        mandatory=True,
        is_approved=True,
    )
    db_session.add(req)
    db_session.commit()

    service = BidVerificationService(db_session)
    await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    run = db_session.query(ComplianceRun).filter(ComplianceRun.bidder_id == sample_bidder.id).first()
    evals = db_session.query(RuleEvaluation).filter(RuleEvaluation.run_id == run.id).all()
    vers = db_session.query(VerificationResult).filter(VerificationResult.run_id == run.id).all()

    # Delete all Evidence DB rows
    db_session.query(Evidence).filter(Evidence.run_id == run.id).delete()
    db_session.commit()

    matrix, evidence_schema, notice = build_compliance_matrix(db_session, sample_bidder, run, evals, vers)
    assert len(evidence_schema) > 0
    assert len(matrix.rows[0].evidence_refs) > 0
    assert notice is None


def test_missing_snapshot_fields(db_session, sample_tender, sample_bidder):
    """5. Verifies legacy pre-Phase 9 run without snapshot outputs historical_limitations_notice without fabricating snapshot evidence."""
    legacy_run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        input_snapshot_json={},
    )
    db_session.add(legacy_run)
    db_session.commit()

    matrix, evidence_schema, notice = build_compliance_matrix(db_session, sample_bidder, legacy_run, [], [])
    assert notice is not None
    assert "Historical compliance run was completed before snapshot recording" in notice
    assert len(evidence_schema) == 0


def test_no_fabricated_ids_timestamps_snippets(db_session, sample_tender, sample_bidder):
    """6. Verifies EvidenceNormalizationService preserves absent source_text as empty snippet without fabricating synthetic text or IDs."""
    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
    )
    db_session.add(doc)
    db_session.commit()

    fact = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="5000000",
        source_text=None,
    )
    db_session.add(fact)
    db_session.commit()

    ev = EvidenceNormalizationService.normalize_fact_evidence(
        db=db_session,
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        fact=fact,
        document=doc,
    )

    assert ev.snippet == ""
    assert ev.provider_identifier is None
    assert ev.source_reference is None


@pytest.mark.asyncio
async def test_multiple_same_field_evidence_inputs(db_session, sample_tender, sample_bidder):
    """7. Verifies when multiple non-conflicting facts exist for the same field, only the actual contributing input is linked."""
    req = TenderRequirement(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        clause="3.1",
        requirement_type=RequirementType.TURNOVER,
        field="financial.average_annual_turnover",
        operator=OperatorEnum.GTE,
        expected_value="4000000",
        mandatory=True,
        is_approved=True,
    )
    db_session.add(req)

    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
    )
    db_session.add(doc)
    db_session.commit()

    # Fact 1: 50 Lakhs (used for evaluation)
    fact1 = ExtractedFact(
        id="fact-50lakhs",
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="5000000",
    )
    # Fact 2: 50 Lakhs (same normalized value)
    fact2 = ExtractedFact(
        id="fact-50lakhs-dup",
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="5000000",
    )
    db_session.add_all([fact1, fact2])
    db_session.commit()

    service = BidVerificationService(db_session)
    overview = await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    eval_turnover = overview.rule_evaluations[0]
    # Only 1 evidence ID (the primary contributing fact) is linked, not all duplicate facts
    assert len(eval_turnover.evidence_ids) == 1


@pytest.mark.asyncio
async def test_exact_evaluation_evidence_linkage(db_session, sample_tender, sample_bidder):
    """8. Verifies evaluation with conflicting facts links both contributing fact evidence IDs because both caused CONFLICTING_FACTS."""
    req = TenderRequirement(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        clause="3.1",
        requirement_type=RequirementType.TURNOVER,
        field="financial.average_annual_turnover",
        operator=OperatorEnum.GTE,
        expected_value="4000000",
        mandatory=True,
        is_approved=True,
    )
    db_session.add(req)

    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
    )
    db_session.add(doc)
    db_session.commit()

    fact1 = ExtractedFact(
        id="fact-50lakhs",
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="5000000",
    )
    fact2 = ExtractedFact(
        id="fact-90lakhs",
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="9000000",
    )
    db_session.add_all([fact1, fact2])
    db_session.commit()

    service = BidVerificationService(db_session)
    overview = await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    eval_turnover = overview.rule_evaluations[0]
    assert eval_turnover.status == ComplianceStatus.REVIEW_REQUIRED
    # Both conflicting facts contributed to CONFLICTING_FACTS, so both evidence IDs are linked
    assert len(eval_turnover.evidence_ids) == 2


def test_document_demo_trust_semantics(db_session, sample_tender, sample_bidder):
    """9. Verifies DOCUMENT and DEMO modes maintain truthful source types and status without simulating live registry evidence."""
    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
    )
    db_session.add(doc)
    db_session.commit()

    fact = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="5000000",
    )
    db_session.add(fact)
    db_session.commit()

    ev_doc = EvidenceNormalizationService.normalize_fact_evidence(
        db=db_session,
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        fact=fact,
        document=doc,
    )
    assert ev_doc.verification_mode == VerificationMode.DOCUMENT
    assert ev_doc.verification_status == VerificationStatus.UNVERIFIED
    assert ev_doc.source_type == "DOCUMENT"

    ver_demo = VerificationResult(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        field="general.gstin",
        claimed_value="27AAACA12341ZV",
        verified_value="27AAACA12341ZV",
        status=VerificationStatus.VERIFIED,
        source="GST_DEMO_DATA",
        mode=VerificationMode.DEMO,
        checked_at=datetime.now(timezone.utc),
    )
    db_session.add(ver_demo)
    db_session.commit()

    ev_demo = EvidenceNormalizationService.normalize_verification_evidence(
        db=db_session,
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        verification=ver_demo,
    )
    assert ev_demo.verification_mode == VerificationMode.DEMO
    assert ev_demo.source_type == "DEMO"


def test_cross_bidder_tender_document_run_ownership(db_session, sample_tender, sample_bidder):
    """10. Verifies EvidenceNormalizationService validate_ownership rejects cross-bidder, cross-tender, cross-document, and cross-run links."""
    other_tender = Tender(
        id=str(uuid.uuid4()),
        tender_number="TNT-OTHER-999",
        title="Other Tender",
    )
    other_bidder = Bidder(
        id=str(uuid.uuid4()),
        tender_id=other_tender.id,
        bidder_name="Other Corp",
    )
    db_session.add_all([other_tender, other_bidder])
    db_session.commit()

    # 1. Cross-tender bidder
    with pytest.raises(EvidenceOwnershipError, match="does not belong to target tender"):
        EvidenceNormalizationService.validate_ownership(
            db=db_session,
            bidder_id=other_bidder.id,
            tender_id=sample_tender.id,
        )

    # 2. Cross-bidder document
    other_doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=other_bidder.id,
        document_type=DocumentType.GST_CERT,
        storage_uri="bidders/other/gst.pdf",
        filename="gst.pdf",
    )
    db_session.add(other_doc)
    db_session.commit()

    with pytest.raises(EvidenceOwnershipError, match="does not match target bidder_id"):
        EvidenceNormalizationService.validate_ownership(
            db=db_session,
            bidder_id=sample_bidder.id,
            tender_id=sample_tender.id,
            document=other_doc,
        )


def test_evidence_persistence_rollback(db_session, sample_tender, sample_bidder):
    """11. Verifies EvidenceNormalizationService stages objects without committing, allowing session rollback on error."""
    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
    )
    db_session.add(doc)
    db_session.commit()

    fact = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="5000000",
    )
    db_session.add(fact)
    db_session.commit()

    ev = EvidenceNormalizationService.normalize_fact_evidence(
        db=db_session,
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        fact=fact,
        document=doc,
    )
    assert ev in db_session.new

    # Roll back session
    db_session.rollback()

    # Evidence row was not committed to DB
    count = db_session.query(Evidence).filter(Evidence.id == ev.id).count()
    assert count == 0


@pytest.mark.asyncio
async def test_failed_snapshot_persistence_does_not_complete_run(db_session, sample_tender, sample_bidder, monkeypatch):
    """12. Verifies that if snapshot persistence or workflow execution fails, ComplianceRun is marked FAILED and NOT COMPLETED."""
    req = TenderRequirement(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        clause="1.1",
        requirement_type=RequirementType.GST,
        field="general.gstin",
        operator=OperatorEnum.EXISTS,
        expected_value="true",
        mandatory=True,
        is_approved=True,
    )
    db_session.add(req)
    db_session.commit()

    service = BidVerificationService(db_session)

    # Monkeypatch compute_overall_status to raise an error during execution
    def mock_fail(*args, **kwargs):
        raise RuntimeError("Simulated workflow failure during status computation.")

    monkeypatch.setattr(service, "compute_overall_status", mock_fail)

    with pytest.raises(RuntimeError, match="Simulated workflow failure"):
        await service.run_verification_workflow(
            bidder_id=sample_bidder.id,
            actor_id="admin-1",
            actor_role="ADMIN",
        )

    run = db_session.query(ComplianceRun).filter(ComplianceRun.bidder_id == sample_bidder.id).first()
    assert run is not None
    assert run.execution_status == JobStatus.FAILED


def test_current_human_decision_clearly_separated_from_historical_report(db_session, sample_bidder):
    """13. Verifies recording a HumanDecision creates an officer audit entry separate from automated compliance."""
    decision = HumanDecision(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        status=HumanDecisionStatus.QUALIFIED,
        reason_code="DOCUMENT_VERIFIED_BY_OFFICER",
        remarks="All documents manually checked.",
        officer_id="officer-101",
        officer_name="Officer Smith",
    )
    db_session.add(decision)
    db_session.commit()

    db_session.refresh(sample_bidder)
    assert decision.status == HumanDecisionStatus.QUALIFIED
    assert decision.officer_id == "officer-101"


def test_missing_snapshot_fields_and_stable_reconstruction(db_session, sample_bidder):
    """14. Verifies missing snapshot fields return explicit limitations notice without crashing or fabricating data, and repeated reads are stable."""
    run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_bidder.tender_id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        completed_at=datetime(2026, 1, 1, 10, 0, 5, tzinfo=timezone.utc),
        created_at=datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        input_snapshot_json={
            "snapshot_version": "1.0",
            # missing "approved_requirements", "facts", "verifications", "evidence"
        },
    )
    db_session.add(run)
    db_session.commit()

    matrix1, ev_schema1, notice1 = build_compliance_matrix(db_session, sample_bidder, run, [], [])
    matrix2, ev_schema2, notice2 = build_compliance_matrix(db_session, sample_bidder, run, [], [])

    assert notice1 is not None
    assert "missing or malformed required sections" in notice1
    # Stable repeated reads
    assert matrix1.model_dump() == matrix2.model_dump()
    assert notice1 == notice2
    assert len(ev_schema1) == len(ev_schema2)


def test_no_fabricated_evidence_ids_or_timestamps(db_session, sample_bidder):
    """15. Verifies reconstructed evidence retains exact snapshot IDs and timestamps without fabricating random UUIDs or now()."""
    run_time = datetime(2026, 2, 1, 12, 0, 0, tzinfo=timezone.utc)
    ev_id = "ev-exact-id-123"

    run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_bidder.tender_id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=run_time,
        completed_at=run_time,
        created_at=run_time,
        input_snapshot_json={
            "snapshot_version": "1.0",
            "approved_requirements": [],
            "facts": [],
            "verifications": [],
            "evidence": [
                {
                    "id": ev_id,
                    "entity_type": "EXTRACTED_FACT",
                    "entity_id": "fact-1",
                    "snippet": "Exact snippet text",
                    "created_at": "2026-02-01T12:00:00+00:00",
                }
            ],
            "exact_evaluation_linkage": [],
        },
    )
    db_session.add(run)
    db_session.commit()

    _, evidence_list, _ = build_compliance_matrix(db_session, sample_bidder, run, [], [])
    assert len(evidence_list) == 1
    ev_item = evidence_list[0]
    assert ev_item.id == ev_id
    assert ev_item.snippet == "Exact snippet text"
    assert ev_item.created_at == run_time


def test_verification_evidence_snippet_separation(db_session, sample_tender, sample_bidder):
    """16. Verifies normalize_verification_evidence keeps snippet as empty string and stores reference/error in distinct metadata fields."""
    ver = VerificationResult(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        field="general.gstin",
        claimed_value="27AAACA12341ZV",
        verified_value=None,
        status=VerificationStatus.SERVICE_ERROR,
        source="GST_AUTHORIZED_API",
        mode=VerificationMode.LIVE,
        checked_at=datetime.now(timezone.utc),
        verification_reference="GST-REF-9999",
        error_message="Gateway 503 Service Unavailable",
    )
    db_session.add(ver)
    db_session.commit()

    ev = EvidenceNormalizationService.normalize_verification_evidence(
        db=db_session,
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        verification=ver,
    )

    # Truthful source fields
    assert ev.snippet == ""
    assert ev.source_reference == "GST-REF-9999"
    assert ev.location_metadata.get("error_message") == "Gateway 503 Service Unavailable"
    assert ev.location_metadata.get("verification_reference") == "GST-REF-9999"
    assert ev.page_number is None


def test_fail_closed_ownership_missing_entities_and_cross_run(db_session, sample_tender, sample_bidder):
    """17. Verifies validate_ownership fails closed when target bidder, tender, document, or run do not exist or mismatch."""
    # 1. Missing bidder in DB
    with pytest.raises(EvidenceOwnershipError, match="Target bidder 'nonexistent-bidder' not found"):
        EvidenceNormalizationService.validate_ownership(
            db=db_session,
            bidder_id="nonexistent-bidder",
            tender_id=sample_tender.id,
        )

    # 2. Missing tender in DB
    with pytest.raises(EvidenceOwnershipError, match="Target tender 'nonexistent-tender' not found"):
        EvidenceNormalizationService.validate_ownership(
            db=db_session,
            bidder_id=sample_bidder.id,
            tender_id="nonexistent-tender",
        )

    # 3. Cross-run verification
    run1 = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        execution_status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(run1)
    db_session.commit()

    run2 = ComplianceRun(
        id="other-run-id-999",
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        execution_status=JobStatus.RUNNING,
        started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(run2)
    db_session.commit()

    ver_other_run = VerificationResult(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        run_id="other-run-id-999",
        field="general.gstin",
        status=VerificationStatus.VERIFIED,
        source="GST_DEMO_DATA",
        mode=VerificationMode.DEMO,
        checked_at=datetime.now(timezone.utc),
    )
    db_session.add(ver_other_run)
    db_session.commit()

    with pytest.raises(EvidenceOwnershipError, match="does not match target run_id"):
        EvidenceNormalizationService.validate_ownership(
            db=db_session,
            bidder_id=sample_bidder.id,
            tender_id=sample_tender.id,
            verification=ver_other_run,
            run=run1,
        )


def test_no_field_based_evidence_fallback_on_unresolvable_linkage(db_session, sample_bidder):
    """18. Verifies build_compliance_matrix attaches NO evidence citations when exact evaluation linkage is unresolvable or missing, even if matching fields exist."""
    run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_bidder.tender_id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        input_snapshot_json={
            "snapshot_version": "1.0",
            "approved_requirements": [
                {
                    "id": "req-1",
                    "clause": "3.1",
                    "requirement_type": "TURNOVER",
                    "field": "financial.average_annual_turnover",
                    "operator": "GTE",
                    "expected_value": "5000000",
                    "mandatory": True,
                }
            ],
            "facts": [
                {
                    "id": "fact-1",
                    "field": "financial.average_annual_turnover",
                    "value": "6000000",
                    "source_text": "Turnover 60L",
                }
            ],
            "verifications": [
                {
                    "id": "ver-1",
                    "field": "financial.average_annual_turnover",
                    "status": "VERIFIED",
                    "verified_value": "6000000",
                }
            ],
            "evidence": [],
            "exact_evaluation_linkage": [
                {
                    "requirement_id": "req-1",
                    "status": ComplianceStatus.PASS,
                    "reason_code": "THRESHOLD_MET",
                    "observed_value": "6000000",
                    "evidence_ids": [],  # Unresolvable / empty linkage
                }
            ],
        },
    )
    db_session.add(run)
    db_session.commit()

    matrix, ev_schema, notice = build_compliance_matrix(db_session, sample_bidder, run, [], [])
    row = matrix.rows[0]
    # References MUST be empty because exact evaluation linkage has no evidence_ids
    assert row.evidence_refs == []
    assert row.verification_refs == []
    assert row.source_refs == []


def test_missing_requirement_definition_leaves_fields_none(db_session, sample_bidder):
    """19. Verifies build_compliance_matrix leaves requirement fields as None without inventing default values when missing from snapshot."""
    run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_bidder.tender_id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        input_snapshot_json={
            "snapshot_version": "1.0",
            "approved_requirements": [],  # req-missing is omitted
            "facts": [],
            "verifications": [],
            "evidence": [],
            "exact_evaluation_linkage": [
                {
                    "requirement_id": "req-missing",
                    "status": ComplianceStatus.UNKNOWN,
                    "reason_code": "REQUIREMENT_NOT_FOUND",
                    "observed_value": None,
                    "evidence_ids": [],
                }
            ],
        },
    )
    db_session.add(run)
    db_session.commit()

    matrix, ev_schema, notice = build_compliance_matrix(db_session, sample_bidder, run, [], [])
    row = matrix.rows[0]
    assert row.requirement_id == "req-missing"
    assert row.clause is None
    assert row.requirement_type is None
    assert row.field is None
    assert row.operator is None
    assert row.expected_value is None
    assert row.unit is None
    assert row.mandatory is None
    assert notice is not None
    assert "Historical requirement definition for requirement_id 'req-missing' missing from run snapshot" in notice


def test_missing_evidence_timestamp_remains_none(db_session, sample_bidder):
    """20. Verifies reconstructed evidence with missing timestamp retains None without substituting run.created_at."""
    run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_bidder.tender_id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        input_snapshot_json={
            "snapshot_version": "1.0",
            "approved_requirements": [],
            "facts": [],
            "verifications": [],
            "evidence": [
                {
                    "id": "ev-no-timestamp",
                    "entity_type": "EXTRACTED_FACT",
                    "snippet": "No timestamp snippet",
                    # created_at is omitted
                }
            ],
            "exact_evaluation_linkage": [],
        },
    )
    db_session.add(run)
    db_session.commit()

    _, evidence_list, _ = build_compliance_matrix(db_session, sample_bidder, run, [], [])
    assert len(evidence_list) == 1
    assert evidence_list[0].created_at is None


@pytest.mark.asyncio
async def test_report_verification_results_snapshot_authority(db_session, sample_tender, sample_bidder):
    """21. Verifies get_bidder_report reads verification results strictly from snapshot, unaffected by DB row mutations."""
    from app.api.v1.bidders import get_bidder_report
    from app.auth.dependencies import AuthenticatedPrincipal
    from app.schemas.canonical import UserRole

    ver_id = str(uuid.uuid4())
    run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        input_snapshot_json={
            "snapshot_version": "1.0",
            "approved_requirements": [],
            "facts": [],
            "verifications": [
                {
                    "id": ver_id,
                    "bidder_id": sample_bidder.id,
                    "field": "general.gstin",
                    "claimed_value": "27AAACA12341ZV",
                    "verified_value": "27AAACA12341ZV",
                    "status": "VERIFIED",
                    "source": "GST_DEMO_DATA",
                    "mode": "DEMO",
                    "checked_at": "2026-02-01T12:00:00+00:00",
                }
            ],
            "evidence": [],
            "exact_evaluation_linkage": [],
        },
    )
    db_session.add(run)

    # DB row with different status (mutated after run)
    ver_db = VerificationResult(
        id=ver_id,
        bidder_id=sample_bidder.id,
        run_id=run.id,
        field="general.gstin",
        claimed_value="27AAACA12341ZV",
        verified_value="MUTATED_VALUE",
        status=VerificationStatus.MISMATCH,
        source="MUTATED_SOURCE",
        mode=VerificationMode.LIVE,
        checked_at=datetime.now(timezone.utc),
    )
    db_session.add(ver_db)
    db_session.commit()

    principal = AuthenticatedPrincipal(user_id="test-user", role=UserRole.ADMIN)
    report = await get_bidder_report(id=sample_bidder.id, run_id=run.id, principal=principal, db=db_session)

    # Report verification results MUST reflect snapshot values (VERIFIED), not DB row (MISMATCH)
    assert len(report.verification_results) == 1
    snapshot_v = report.verification_results[0]
    assert snapshot_v.id == ver_id
    assert snapshot_v.status == VerificationStatus.VERIFIED
    assert snapshot_v.verified_value == "27AAACA12341ZV"


@pytest.mark.asyncio
async def test_valid_empty_verification_snapshot_remains_empty(db_session, sample_tender, sample_bidder):
    """22. Verifies a valid empty verification snapshot list [] remains [] and never loads live DB rows."""
    from app.api.v1.bidders import get_bidder_report
    from app.auth.dependencies import AuthenticatedPrincipal
    from app.schemas.canonical import UserRole

    run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        input_snapshot_json={
            "snapshot_version": "1.0",
            "approved_requirements": [],
            "facts": [],
            "verifications": [],  # Valid empty list
            "evidence": [],
            "exact_evaluation_linkage": [],
        },
    )
    db_session.add(run)

    # Add a live DB row that should NEVER be loaded
    ver_db = VerificationResult(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        run_id=run.id,
        field="general.gstin",
        status=VerificationStatus.VERIFIED,
        source="LIVE_DB",
        mode=VerificationMode.LIVE,
        checked_at=datetime.now(timezone.utc),
    )
    db_session.add(ver_db)
    db_session.commit()

    principal = AuthenticatedPrincipal(user_id="test-user", role=UserRole.ADMIN)
    report = await get_bidder_report(id=sample_bidder.id, run_id=run.id, principal=principal, db=db_session)

    # Must remain empty [], NOT load ver_db from live DB
    assert report.verification_results == []
    assert report.historical_limitations_notice is None


@pytest.mark.asyncio
async def test_missing_or_malformed_phase9_verifications_never_loads_current_db(db_session, sample_tender, sample_bidder):
    """23. Verifies a Phase 9 snapshot with missing or malformed verifications produces historical limitation notice and never loads current DB rows."""
    from app.api.v1.bidders import get_bidder_report
    from app.auth.dependencies import AuthenticatedPrincipal
    from app.schemas.canonical import UserRole

    run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        input_snapshot_json={
            "snapshot_version": "1.0",
            "approved_requirements": [],
            "facts": [],
            "verifications": "MALFORMED_STRING_NOT_A_LIST",  # Malformed
            "evidence": [],
            "exact_evaluation_linkage": [],
        },
    )
    db_session.add(run)

    ver_db = VerificationResult(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        run_id=run.id,
        field="general.gstin",
        status=VerificationStatus.VERIFIED,
        source="LIVE_DB",
        mode=VerificationMode.LIVE,
        checked_at=datetime.now(timezone.utc),
    )
    db_session.add(ver_db)
    db_session.commit()

    principal = AuthenticatedPrincipal(user_id="test-user", role=UserRole.ADMIN)
    report = await get_bidder_report(id=sample_bidder.id, run_id=run.id, principal=principal, db=db_session)

    # Must NOT load ver_db from live DB
    assert report.verification_results == []
    assert report.historical_limitations_notice is not None
    assert "verifications" in report.historical_limitations_notice


def test_existing_exact_evidence_linkage_remains_intact(db_session, sample_bidder):
    """24. Verifies build_compliance_matrix correctly attaches exact evidence citations when linked by evidence_ids."""
    run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_bidder.tender_id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        input_snapshot_json={
            "snapshot_version": "1.0",
            "approved_requirements": [
                {
                    "id": "req-1",
                    "clause": "3.1",
                    "requirement_type": "TURNOVER",
                    "field": "financial.average_annual_turnover",
                    "operator": "GTE",
                    "expected_value": "5000000",
                    "mandatory": True,
                }
            ],
            "facts": [
                {
                    "id": "fact-1",
                    "document_id": "doc-1",
                    "field": "financial.average_annual_turnover",
                    "value": "6000000",
                    "source_page": 2,
                    "source_text": "Turnover is 60 Lakhs INR",
                    "confidence": 0.99,
                }
            ],
            "verifications": [
                {
                    "id": "ver-1",
                    "field": "financial.average_annual_turnover",
                    "status": "VERIFIED",
                    "verified_value": "6000000",
                }
            ],
            "evidence": [
                {
                    "id": "ev-1",
                    "extracted_fact_id": "fact-1",
                    "verification_result_id": "ver-1",
                    "snippet": "Turnover is 60 Lakhs INR",
                    "source_type": "DOCUMENT",
                    "page_number": 2,
                }
            ],
            "exact_evaluation_linkage": [
                {
                    "requirement_id": "req-1",
                    "status": ComplianceStatus.PASS,
                    "reason_code": "THRESHOLD_MET",
                    "observed_value": "6000000",
                    "evidence_ids": ["ev-1"],
                }
            ],
        },
    )
    db_session.add(run)
    db_session.commit()

    matrix, ev_schema, notice = build_compliance_matrix(db_session, sample_bidder, run, [], [])
    row = matrix.rows[0]
    assert len(row.evidence_refs) == 1
    assert row.evidence_refs[0]["evidence_id"] == "ev-1"
    assert len(row.verification_refs) == 1
    assert row.verification_refs[0]["id"] == "ver-1"
    assert len(row.source_refs) == 1
    assert row.source_refs[0]["source_text"] == "Turnover is 60 Lakhs INR"



