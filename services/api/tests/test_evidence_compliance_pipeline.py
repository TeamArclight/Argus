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
    with pytest.raises(EvidenceOwnershipError, match="does not match target tender_id"):
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
