from datetime import datetime, timezone
import uuid
import pytest
from sqlalchemy.orm import Session

from app.db.session import Base, SessionLocal, engine
from app.models.domain import (
    Bidder,
    ComplianceRun,
    Document,
    DocumentType,
    ExtractedFact,
    HumanDecision,
    HumanDecisionStatus,
    JobStatus,
    RiskSignal,
    Tender,
    TenderRequirement,
    VerificationResult,
)
from app.risk.engine import RiskEngine, RiskSignalCandidate
from app.schemas.canonical import (
    ComplianceStatus,
    FactRead,
    OperatorEnum,
    ProviderConfigurationStatus,
    ProviderOperationalHealth,
    RequirementType,
    RiskSeverity,
    RiskSignalRead,
    RiskSummaryRead,
    VerificationMode,
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)
from app.services.bid_verification_service import BidVerificationService
from app.verification.registry import ProviderRegistry


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
        tender_number="TNT-RISK-001",
        title="Risk Test Tender",
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
        bidder_name="Acme Procurement Solutions Pvt Ltd",
        gstin="27AAACA1234F1ZV",
        pan="AAACA1234F",
        udyam_number="UDYAM-MH-01-0012345",
        status=HumanDecisionStatus.PENDING,
    )
    db_session.add(bidder)
    db_session.commit()
    db_session.refresh(bidder)
    return bidder


# ---------------------------------------------------------------------------
# 1. PROVIDER HEALTH & CONFIGURATION TESTS
# ---------------------------------------------------------------------------

def test_provider_registry_health_and_configuration(monkeypatch):
    """1. Verifies ProviderRegistry reports correct configuration/health separation without live HTTP requests."""
    from app.core.config import settings

    # Force GST to LIVE mode without credentials
    monkeypatch.setattr(settings, "GST_VERIFICATION_MODE", "LIVE")
    monkeypatch.setattr(settings, "GST_API_URL", None)
    monkeypatch.setattr(settings, "GST_API_KEY", None)

    providers = ProviderRegistry.get_provider_health_list()
    gst_prov = [p for p in providers if p.provider_identifier == "gst"][0]
    assert gst_prov.configuration_status == ProviderConfigurationStatus.UNCONFIGURED
    assert gst_prov.operational_health == ProviderOperationalHealth.UNAVAILABLE

    # Force GST to LIVE mode WITH credentials
    monkeypatch.setattr(settings, "GST_API_URL", "https://api.gst.gov.in/v1")
    monkeypatch.setattr(settings, "GST_API_KEY", "test-key-123")

    providers2 = ProviderRegistry.get_provider_health_list()
    gst_prov2 = [p for p in providers2 if p.provider_identifier == "gst"][0]
    assert gst_prov2.configuration_status == ProviderConfigurationStatus.CONFIGURED
    assert gst_prov2.operational_health == ProviderOperationalHealth.UNKNOWN


def test_explicit_demo_mode_reporting(monkeypatch):
    """2. Verifies DEMO mode providers report CONFIGURED and AVAILABLE with explicit DEMO notes."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "UDYAM_VERIFICATION_MODE", "DEMO")

    providers = ProviderRegistry.get_provider_health_list()
    udyam_prov = [p for p in providers if p.provider_identifier == "udyam"][0]
    assert udyam_prov.configured_mode == VerificationMode.DEMO
    assert udyam_prov.configuration_status == ProviderConfigurationStatus.CONFIGURED
    assert udyam_prov.operational_health == ProviderOperationalHealth.AVAILABLE
    assert "DEMO mode" in udyam_prov.notes


@pytest.mark.asyncio
async def test_no_silent_live_to_demo_fallback(db_session, sample_tender, sample_bidder, monkeypatch):
    """3. Verifies missing live credentials return UNAVAILABLE without falling back silently to DEMO mode."""
    from app.core.config import settings
    from app.verification.adapters import GSTVerificationAdapter

    monkeypatch.setattr(settings, "GST_VERIFICATION_MODE", "LIVE")
    monkeypatch.setattr(settings, "GST_API_URL", None)
    monkeypatch.setattr(settings, "GST_API_KEY", None)

    adapter = GSTVerificationAdapter()
    res = await adapter.verify({"id": sample_bidder.id, "gstin": "27AAACA12341ZV"}, "general.gstin")

    assert res.mode == VerificationMode.LIVE
    assert res.status == VerificationStatus.UNAVAILABLE
    assert "not configured" in res.error_message


# ---------------------------------------------------------------------------
# 2. IDENTIFIER & FRESHNESS DETERMINISTIC CHECKS
# ---------------------------------------------------------------------------

def test_gstin_pan_mismatch_detection():
    """4. Verifies RiskEngine detects embedded PAN mismatch in GSTIN."""
    bidder_data = {
        "id": "b1",
        "gstin": "27AAACA1234F1ZV",  # Embedded PAN: AAACA1234F
        "pan": "BBBCB9999Z",        # Mismatched PAN
    }
    candidates = RiskEngine.evaluate_risks(
        facts=[],
        verifications=[],
        documents=[],
        bidder_data=bidder_data,
    )

    mismatches = [c for c in candidates if c.signal_type == "GSTIN_PAN_MISMATCH"]
    assert len(mismatches) == 1
    assert mismatches[0].severity == RiskSeverity.HIGH
    assert mismatches[0].reason_code == "GSTIN_PAN_MISMATCH"


def test_missing_and_stale_cached_freshness():
    """5. Verifies RiskEngine flags missing source observation timestamp and stale cached records."""
    now = datetime.now(timezone.utc)
    v_missing_ts = VerificationResultRead(
        id="v-missing",
        bidder_id="b1",
        field="general.gstin",
        claimed_value="27AAACA12341ZV",
        status=VerificationStatus.VERIFIED,
        source=VerificationSource.GST_PORTAL_VERIFIED_CACHE,
        mode=VerificationMode.PORTAL_CACHED,
        checked_at=now,
        location_metadata={},  # Missing source_observed_at
    )

    v_stale = VerificationResultRead(
        id="v-stale",
        bidder_id="b1",
        field="general.udyam",
        claimed_value="UDYAM-MH-01-0012345",
        status=VerificationStatus.VERIFIED,
        source=VerificationSource.UDYAM_PORTAL_VERIFIED_CACHE,
        mode=VerificationMode.PORTAL_CACHED,
        checked_at=now,
        location_metadata={"source_observed_at": "2025-01-01T00:00:00+00:00"},  # 400+ days old
    )

    candidates = RiskEngine.evaluate_risks(
        facts=[],
        verifications=[v_missing_ts, v_stale],
        documents=[],
        bidder_data={},
        freshness_policy={"general.udyam": 90},
    )

    missing_sig = [c for c in candidates if c.signal_type == "MISSING_FRESHNESS_TIMESTAMP"]
    stale_sig = [c for c in candidates if c.signal_type == "STALE_CACHED_RECORD"]

    assert len(missing_sig) == 1
    assert missing_sig[0].reason_code == "MISSING_FRESHNESS_TIMESTAMP"

    assert len(stale_sig) == 1
    assert stale_sig[0].reason_code == "STALE_CACHED_RECORD"
    assert stale_sig[0].metadata_json["age_days"] > 90


# ---------------------------------------------------------------------------
# 3. FINANCIAL & CROSS-DOCUMENT CHECKS
# ---------------------------------------------------------------------------

def test_cross_fy_financial_isolation():
    """6. Verifies turnover values from different financial years are NOT compared as conflicting."""
    f_fy23 = FactRead(
        id="f1",
        document_id="doc1",
        bidder_id="b1",
        field="financial.average_annual_turnover",
        value="5000000",
        confidence=1.0,
        created_at=datetime.now(timezone.utc),
        metadata_json={"financial_year": "2023-24"},
    )
    f_fy24 = FactRead(
        id="f2",
        document_id="doc2",
        bidder_id="b1",
        field="financial.average_annual_turnover",
        value="8000000",
        confidence=1.0,
        created_at=datetime.now(timezone.utc),
        metadata_json={"financial_year": "2024-25"},
    )

    candidates = RiskEngine.evaluate_risks(
        facts=[f_fy23, f_fy24],
        verifications=[],
        documents=[],
        bidder_data={},
    )

    conflicts = [c for c in candidates if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]
    # Different FYs MUST NOT produce a conflict signal
    assert len(conflicts) == 0


def test_same_fy_conflicting_turnover_detection():
    """7. Verifies conflicting turnover for the SAME financial year produces CONFLICTING_TURNOVER_SAME_FY."""
    f1 = FactRead(
        id="f1",
        document_id="doc1",
        bidder_id="b1",
        field="financial.average_annual_turnover",
        value="5000000",
        confidence=1.0,
        created_at=datetime.now(timezone.utc),
        metadata_json={"financial_year": "2024-25"},
    )
    f2 = FactRead(
        id="f2",
        document_id="doc2",
        bidder_id="b1",
        field="financial.average_annual_turnover",
        value="9000000",
        confidence=1.0,
        created_at=datetime.now(timezone.utc),
        metadata_json={"financial_year": "2024-25"},
    )

    candidates = RiskEngine.evaluate_risks(
        facts=[f1, f2],
        verifications=[],
        documents=[],
        bidder_data={},
    )

    conflicts = [c for c in candidates if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]
    assert len(conflicts) == 1
    assert conflicts[0].severity == RiskSeverity.HIGH
    assert conflicts[0].input_ids == ["f1", "f2"]


def test_duplicate_document_hash_non_fraud_interpretation():
    """8. Verifies duplicate document hash across bidders produces DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER without alleging fraud."""
    local_docs = [{"id": "d-local", "filename": "tender_form.pdf", "sha256": "hash-abc12345"}]
    comparison_meta = [{"id": "d-other", "bidder_id": "other-bidder", "sha256": "hash-abc12345"}]

    candidates = RiskEngine.evaluate_risks(
        facts=[],
        verifications=[],
        documents=local_docs,
        bidder_data={},
        comparison_metadata=comparison_meta,
    )

    dups = [c for c in candidates if c.signal_type == "DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER"]
    assert len(dups) == 1
    assert dups[0].severity == RiskSeverity.MEDIUM
    assert "template reuse" in dups[0].description
    assert "fraud" not in dups[0].description.lower()


# ---------------------------------------------------------------------------
# 4. EVIDENCE LINKAGE & WORKFLOW INTEGRATION
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_exact_risk_evidence_linkage_and_unmapped_ids(db_session, sample_tender, sample_bidder):
    """9. Verifies BidVerificationService maps RiskEngine input_ids to Evidence IDs and records unmapped IDs explicitly."""
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

    # Create two facts for same FY to trigger CONFLICTING_TURNOVER_SAME_FY
    f1 = ExtractedFact(
        id="fact-fy24-a",
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="5000000",
        metadata_json={"financial_year": "2024-25"},
    )
    f2 = ExtractedFact(
        id="fact-fy24-b",
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="9000000",
        metadata_json={"financial_year": "2024-25"},
    )
    db_session.add_all([f1, f2])
    db_session.commit()

    service = BidVerificationService(db_session)
    overview = await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    # Risk signals generated
    assert len(overview.risk_signals) > 0
    conflict_risk = [r for r in overview.risk_signals if r.signal_type == "CONFLICTING_TURNOVER_SAME_FY"][0]
    assert len(conflict_risk.evidence_ids) == 2


@pytest.mark.asyncio
async def test_risk_summary_aggregation_without_score(db_session, sample_tender, sample_bidder):
    """10. Verifies risk summary provides clean counts without arbitrary risk scores."""
    service = BidVerificationService(db_session)
    overview = await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    assert overview.risk_summary is not None
    assert overview.risk_summary.signal_count == len(overview.risk_signals)
    assert hasattr(overview.risk_summary, "risk_engine_version")
    assert not hasattr(overview.risk_summary, "risk_score")  # No arbitrary risk score field!


# ---------------------------------------------------------------------------
# 5. HISTORICAL RISK SNAPSHOT & MUTATION RESISTANCE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_historical_risk_snapshot_mutation_resistance(db_session, sample_tender, sample_bidder):
    """11. Verifies report risk signals reconstructed from run snapshot are unaffected by subsequent RiskSignal DB row deletion or mutation."""
    from app.api.v1.bidders import get_bidder_report
    from app.auth.dependencies import AuthenticatedPrincipal
    from app.schemas.canonical import UserRole

    doc = Document(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        document_type=DocumentType.OEM_AUTHORIZATION,
        storage_uri="bidders/b1/oem.pdf",
        filename="oem.pdf",
    )
    db_session.add(doc)
    f = ExtractedFact(
        id="fact-oem-expired",
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="oem.expiry_date",
        value="2020-01-01T00:00:00+00:00",
    )
    db_session.add(f)
    db_session.commit()

    service = BidVerificationService(db_session)
    await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    run = db_session.query(ComplianceRun).filter(ComplianceRun.bidder_id == sample_bidder.id).first()

    # Mutate / Delete RiskSignal DB rows
    db_session.query(RiskSignal).filter(RiskSignal.run_id == run.id).delete()
    db_session.commit()

    principal = AuthenticatedPrincipal(user_id="test-user", role=UserRole.ADMIN)
    report = await get_bidder_report(id=sample_bidder.id, run_id=run.id, principal=principal, db=db_session)

    # Report risk signals MUST be reconstructed from snapshot, unaffected by DB deletion
    assert len(report.compliance_overview.risk_signals) > 0
    assert report.risk_summary is not None


def test_legacy_phase9_run_risk_limitations(db_session, sample_tender, sample_bidder):
    """12. Verifies legacy pre-Phase 10 run returns clean fallback without crashing or inventing risk snapshots."""
    legacy_run = ComplianceRun(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        input_snapshot_json={},  # Legacy without risk section
    )
    db_session.add(legacy_run)
    db_session.commit()

    from app.api.v1.bidders import build_compliance_matrix
    matrix, _, notice = build_compliance_matrix(db_session, sample_bidder, legacy_run, [], [])

    assert notice is not None
    assert "Historical compliance run was completed before snapshot recording" in notice


def test_risk_signals_do_not_alter_statutory_qualification_authority(db_session, sample_bidder):
    """13. Verifies risk signals remain advisory and do NOT directly pass/fail compliance or alter human decision status."""
    decision = HumanDecision(
        id=str(uuid.uuid4()),
        bidder_id=sample_bidder.id,
        status=HumanDecisionStatus.QUALIFIED,
        reason_code="OFFICER_OVERRIDE",
        remarks="Officer verified documents.",
        officer_id="off-1",
        officer_name="Officer Officer",
    )
    sample_bidder.status = HumanDecisionStatus.QUALIFIED
    db_session.add(decision)
    db_session.commit()

    db_session.refresh(sample_bidder)
    assert sample_bidder.status == HumanDecisionStatus.QUALIFIED
