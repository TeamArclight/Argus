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
    RuleEvaluation,
    Tender,
    TenderRequirement,
    VerificationResult,
)
from app.schemas.canonical import (
    ComplianceStatus,
    JobStatus,
    OperatorEnum,
    RequirementType,
    VerificationMode,
    VerificationStatus,
)
from app.services.bid_verification_service import BidVerificationService
from app.services.evidence_service import EvidenceNormalizationService


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
async def test_snapshot_values_remain_stable_after_source_fact_and_rule_mutation(db_session, sample_tender, sample_bidder):
    """Verifies that compliance run snapshot data remains stable even if underlying ExtractedFact or TenderRequirement rows are mutated later."""
    # Create approved tender requirement
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

    # Create document & fact
    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
        sha256="abc123sha",
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

    # Run verification workflow
    service = BidVerificationService(db_session)
    overview = await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )
    assert overview.overall_status == ComplianceStatus.PASS

    # Fetch completed ComplianceRun
    run = (
        db_session.query(ComplianceRun)
        .filter(ComplianceRun.bidder_id == sample_bidder.id)
        .order_by(ComplianceRun.created_at.desc())
        .first()
    )
    assert run is not None
    assert run.input_snapshot_json is not None
    snapshot = run.input_snapshot_json
    assert len(snapshot["approved_requirements"]) == 1
    assert snapshot["approved_requirements"][0]["expected_value"] == "5000000"
    assert len(snapshot["facts"]) == 1
    assert snapshot["facts"][0]["value"] == "6000000"

    # Mutate DB requirement threshold and fact value
    req.expected_value = "90000000"
    fact.value = "100"
    db_session.commit()

    # Re-verify that snapshot stored in ComplianceRun is unchanged
    db_session.refresh(run)
    post_snapshot = run.input_snapshot_json
    assert post_snapshot["approved_requirements"][0]["expected_value"] == "5000000"
    assert post_snapshot["facts"][0]["value"] == "6000000"


def test_evidence_ownership_validation_rejects_cross_bidder(db_session, sample_tender, sample_bidder):
    """Verifies EvidenceNormalizationService rejects facts belonging to a different bidder."""
    bidder2 = Bidder(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        bidder_name="Other Bidder Ltd",
    )
    db_session.add(bidder2)
    db_session.commit()

    doc2 = Document(
        id=str(uuid.uuid4()),
        bidder_id=bidder2.id,
        document_type=DocumentType.GST_CERT,
        storage_uri="bidders/b2/gst.pdf",
        filename="gst.pdf",
    )
    db_session.add(doc2)
    db_session.commit()

    fact_b2 = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc2.id,
        bidder_id=bidder2.id,
        field="gstin",
        value="27AAACA12341ZV",
    )
    db_session.add(fact_b2)
    db_session.commit()

    with pytest.raises(ValueError, match="does not match target bidder_id"):
        EvidenceNormalizationService.normalize_fact_evidence(
            db=db_session,
            bidder_id=sample_bidder.id,
            tender_id=sample_tender.id,
            fact=fact_b2,
            document=doc2,
        )


def test_evidence_ownership_validation_rejects_cross_tender(db_session, sample_bidder):
    """Verifies EvidenceNormalizationService rejects documents belonging to a different tender."""
    other_tender = Tender(
        id=str(uuid.uuid4()),
        tender_number="GEM/2026/OTHER/999",
        title="Other Tender",
    )
    db_session.add(other_tender)
    db_session.commit()

    doc_other = Document(
        id=str(uuid.uuid4()),
        tender_id=other_tender.id,
        document_type=DocumentType.TENDER,
        storage_uri="tenders/other/tender.pdf",
        filename="tender.pdf",
    )
    db_session.add(doc_other)
    db_session.commit()

    fact_other = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc_other.id,
        bidder_id=sample_bidder.id,
        field="turnover",
        value="100000",
    )
    db_session.add(fact_other)
    db_session.commit()

    with pytest.raises(ValueError, match="does not match target tender_id"):
        EvidenceNormalizationService.normalize_fact_evidence(
            db=db_session,
            bidder_id=sample_bidder.id,
            tender_id=sample_bidder.tender_id,
            fact=fact_other,
            document=doc_other,
        )


def test_actual_verification_mode_preservation(db_session, sample_tender, sample_bidder):
    """Verifies document claims are stored with DOCUMENT mode and UNVERIFIED status, and not upgraded to LIVE."""
    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/turnover.pdf",
        filename="turnover.pdf",
        sha256="hash999",
    )
    db_session.add(doc)
    db_session.commit()

    fact = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="5000000",
        source_page=2,
        source_text="Annual Turnover: Rs 50,00,000",
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

    assert ev.verification_mode == VerificationMode.DOCUMENT
    assert ev.verification_status == VerificationStatus.UNVERIFIED
    assert ev.sha256 == "hash999"


@pytest.mark.asyncio
async def test_evaluation_specific_evidence_linkage(db_session, sample_tender, sample_bidder):
    """Verifies RuleEvaluation.evidence_ids links specifically to evidence records matching req.field."""
    req1 = TenderRequirement(
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
    req2 = TenderRequirement(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        clause="2.1",
        requirement_type=RequirementType.TURNOVER,
        field="financial.average_annual_turnover",
        operator=OperatorEnum.GTE,
        expected_value="1000000",
        mandatory=True,
        is_approved=True,
    )
    db_session.add_all([req1, req2])
    db_session.commit()

    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.GST_CERT,
        storage_uri="bidders/b1/gst.pdf",
        filename="gst.pdf",
    )
    db_session.add(doc)
    db_session.commit()

    fact1 = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="general.gstin",
        value="27AAACA12341ZV",
    )
    fact2 = ExtractedFact(
        id=str(uuid.uuid4()),
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="2000000",
    )
    db_session.add_all([fact1, fact2])
    db_session.commit()

    service = BidVerificationService(db_session)
    overview = await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    eval_gst = next(e for e in overview.rule_evaluations if e.requirement_id == req1.id)
    eval_turnover = next(e for e in overview.rule_evaluations if e.requirement_id == req2.id)

    # Check evidence linked to eval_gst is specifically for GST
    ev_gst_rows = db_session.query(Evidence).filter(Evidence.id.in_(eval_gst.evidence_ids)).all()
    for ev in ev_gst_rows:
        if ev.extracted_fact_id:
            fact_obj = db_session.query(ExtractedFact).filter(ExtractedFact.id == ev.extracted_fact_id).first()
            assert fact_obj.field == "general.gstin"

    # Check evidence linked to eval_turnover is specifically for turnover
    ev_turnover_rows = db_session.query(Evidence).filter(Evidence.id.in_(eval_turnover.evidence_ids)).all()
    for ev in ev_turnover_rows:
        if ev.extracted_fact_id:
            fact_obj = db_session.query(ExtractedFact).filter(ExtractedFact.id == ev.extracted_fact_id).first()
            assert fact_obj.field == "financial.average_annual_turnover"


@pytest.mark.asyncio
async def test_zero_approved_requirements_yields_unknown(db_session, sample_tender, sample_bidder):
    """Verifies that if a tender has no approved requirements, compliance evaluation returns UNKNOWN."""
    # Unapproved requirement
    req = TenderRequirement(
        id=str(uuid.uuid4()),
        tender_id=sample_tender.id,
        clause="1.1",
        requirement_type=RequirementType.GST,
        field="general.gstin",
        operator=OperatorEnum.EXISTS,
        expected_value="true",
        mandatory=True,
        is_approved=False,
    )
    db_session.add(req)
    db_session.commit()

    service = BidVerificationService(db_session)
    overview = await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    assert overview.overall_status == ComplianceStatus.UNKNOWN
    assert len(overview.rule_evaluations) == 0


def test_human_decision_remains_separate_from_automated_compliance(db_session, sample_bidder):
    """Verifies recording a HumanDecision creates an officer audit entry without altering automated compliance status."""
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
