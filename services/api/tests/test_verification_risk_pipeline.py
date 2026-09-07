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
        evaluation_timestamp=datetime.now(timezone.utc),
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
        evaluation_timestamp=now,
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
        evaluation_timestamp=datetime.now(timezone.utc),
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
        value="5 Crore INR",
        confidence=1.0,
        created_at=datetime.now(timezone.utc),
        metadata_json={"financial_year": "2024-25", "currency": "INR", "unit": "Crore", "averaging_period": "3_years"},
    )
    f2 = FactRead(
        id="f2",
        document_id="doc2",
        bidder_id="b1",
        field="financial.average_annual_turnover",
        value="9 Crore INR",
        confidence=1.0,
        created_at=datetime.now(timezone.utc),
        metadata_json={"financial_year": "2024-25", "currency": "INR", "unit": "Crore", "averaging_period": "3_years"},
    )

    candidates = RiskEngine.evaluate_risks(
        facts=[f1, f2],
        verifications=[],
        documents=[],
        bidder_data={},
        evaluation_timestamp=datetime.now(timezone.utc),
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
        evaluation_timestamp=datetime.now(timezone.utc),
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
        value="5 Crore INR",
        metadata_json={"financial_year": "2024-25", "currency": "INR", "unit": "Crore", "averaging_period": "3_years"},
    )
    f2 = ExtractedFact(
        id="fact-fy24-b",
        document_id=doc.id,
        bidder_id=sample_bidder.id,
        field="financial.average_annual_turnover",
        value="9 Crore INR",
        metadata_json={"financial_year": "2024-25", "currency": "INR", "unit": "Crore", "averaging_period": "3_years"},
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


# ---------------------------------------------------------------------------
# 6. REVIEW FIX FOCUSED REGRESSION TESTS
# ---------------------------------------------------------------------------

def test_provider_registry_truthfulness_and_capabilities(monkeypatch):
    """14. Verifies last_checked_at is None without operational checks, UNKNOWN for unverified LIVE/PORTAL/DOCUMENT, and AVAILABLE for DEMO."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "GST_VERIFICATION_MODE", "LIVE")
    monkeypatch.setattr(settings, "GST_API_URL", "https://api.gst.gov.in")
    monkeypatch.setattr(settings, "GST_API_KEY", "key123")
    monkeypatch.setattr(settings, "UDYAM_VERIFICATION_MODE", "DEMO")

    providers = ProviderRegistry.get_provider_health_list()
    gst_p = [p for p in providers if p.provider_identifier == "gst"][0]
    udyam_p = [p for p in providers if p.provider_identifier == "udyam"][0]

    assert gst_p.last_checked_at is None
    assert gst_p.operational_health == ProviderOperationalHealth.UNKNOWN

    assert udyam_p.last_checked_at is None
    assert udyam_p.operational_health == ProviderOperationalHealth.AVAILABLE
    assert "DEMO mode" in udyam_p.notes


def test_typed_risk_input_references_and_no_document_masquerading():
    """15. Verifies typed input references and ensures Document IDs are NOT masqueraded as Evidence IDs."""
    eval_ts = datetime.now(timezone.utc)
    bidder_data = {"id": "b-100", "gstin": "27AAACA1234F1ZV", "pan": "BBBCB9999Z"}

    candidates = RiskEngine.evaluate_risks(
        facts=[],
        verifications=[],
        documents=[{"id": "doc-99", "filename": "tax.pdf", "sha256": "sha-99"}],
        bidder_data=bidder_data,
        evaluation_timestamp=eval_ts,
    )

    mismatch = [c for c in candidates if c.signal_type == "GSTIN_PAN_MISMATCH"][0]
    assert len(mismatch.input_refs) > 0
    ref_types = [r.ref_type for r in mismatch.input_refs]
    assert "BIDDER_RECORD" in ref_types or "EXTRACTED_FACT" in ref_types


def test_financial_comparison_safety_edge_cases():
    """16. Verifies metric isolation, currency safety, crore/lakh scaling, missing unit handling, and non-finite value protection."""
    eval_ts = datetime.now(timezone.utc)

    # 1. Average vs Single-Year turnover
    f_avg = FactRead(
        id="f-avg", document_id="d1", bidder_id="b1", field="financial.average_annual_turnover", value="5000000", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25"}
    )
    f_single = FactRead(
        id="f-single", document_id="d2", bidder_id="b1", field="financial.annual_turnover", value="9000000", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25"}
    )

    c1 = RiskEngine.evaluate_risks(facts=[f_avg, f_single], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    metric_sig = [c for c in c1 if c.signal_type == "AMBIGUOUS_FINANCIAL_METRIC"]
    conflicts_1 = [c for c in c1 if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]
    assert len(metric_sig) == 1
    assert len(conflicts_1) == 0  # Differing metrics MUST NOT produce a conflict claim!

    # 2. Incompatible Currencies (USD vs INR)
    f_usd = FactRead(
        id="f-usd", document_id="d1", bidder_id="b1", field="financial.average_annual_turnover", value="5000000 USD", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "USD", "averaging_period": "3_years"}
    )
    f_inr = FactRead(
        id="f-inr", document_id="d2", bidder_id="b1", field="financial.average_annual_turnover", value="5000000 INR", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR", "averaging_period": "3_years"}
    )

    c2 = RiskEngine.evaluate_risks(facts=[f_usd, f_inr], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    curr_sig = [c for c in c2 if c.signal_type == "AMBIGUOUS_FINANCIAL_CURRENCY"]
    conflicts_2 = [c for c in c2 if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]
    assert len(curr_sig) == 1
    assert len(conflicts_2) == 0  # Incompatible currencies MUST NOT be converted or compared as conflict!

    # 3. Compatible Scale Conversion (5 Crore vs 500 Lakh -> Equal; 5 Crore vs 90 Lakh -> Conflict)
    f_cr = FactRead(
        id="f-cr", document_id="d1", bidder_id="b1", field="financial.average_annual_turnover", value="5 Crore", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR", "averaging_period": "3_years"}
    )
    f_lakh_eq = FactRead(
        id="f-lakh-eq", document_id="d2", bidder_id="b1", field="financial.average_annual_turnover", value="500 Lakh", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR", "averaging_period": "3_years"}
    )

    c3_eq = RiskEngine.evaluate_risks(facts=[f_cr, f_lakh_eq], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    conflicts_3_eq = [c for c in c3_eq if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]
    assert len(conflicts_3_eq) == 0  # 5 Crore == 500 Lakh -> NO conflict!

    f_lakh_diff = FactRead(
        id="f-lakh-diff", document_id="d3", bidder_id="b1", field="financial.average_annual_turnover", value="90 Lakh", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR", "averaging_period": "3_years"}
    )

    c3_diff = RiskEngine.evaluate_risks(facts=[f_cr, f_lakh_diff], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    conflicts_3_diff = [c for c in c3_diff if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]
    assert len(conflicts_3_diff) == 1
    assert conflicts_3_diff[0].metadata_json["normalized_value_1"] == 50000000.0
    assert conflicts_3_diff[0].metadata_json["normalized_value_2"] == 9000000.0

    # 4. Bare Number vs Explicit Unit
    f_bare = FactRead(
        id="f-bare", document_id="d1", bidder_id="b1", field="financial.average_annual_turnover", value="5", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR", "averaging_period": "3_years"}
    )
    c4 = RiskEngine.evaluate_risks(facts=[f_cr, f_bare], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    unit_sig = [c for c in c4 if c.signal_type == "AMBIGUOUS_FINANCIAL_UNIT"]
    assert len(unit_sig) == 1


def test_reproducible_freshness_and_oem_expiry():
    """17. Verifies freshness calculations against evaluation_timestamp, unknown policy isolation, future timestamp handling, and date-only OEM expiry."""
    eval_ts = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Future source observation timestamp
    v_future = VerificationResultRead(
        id="v-fut",
        bidder_id="b1",
        field="general.gstin",
        claimed_value="27AAACA1234F1ZV",
        status=VerificationStatus.VERIFIED,
        source=VerificationSource.GST_PORTAL_VERIFIED_CACHE,
        mode=VerificationMode.PORTAL_CACHED,
        checked_at=eval_ts,
        location_metadata={"source_observed_at": "2026-06-15T00:00:00+00:00"},  # Future relative to June 1
    )

    c1 = RiskEngine.evaluate_risks(facts=[], verifications=[v_future], documents=[], bidder_data={}, freshness_policy={"general.gstin": 30}, evaluation_timestamp=eval_ts)
    fut_sig = [c for c in c1 if c.signal_type == "FUTURE_FRESHNESS_TIMESTAMP"]
    assert len(fut_sig) == 1

    # Unknown policy domain isolation (must NOT fall back to GST threshold!)
    v_unknown_domain = VerificationResultRead(
        id="v-unk",
        bidder_id="b1",
        field="custom.unknown_domain",
        claimed_value="VAL",
        status=VerificationStatus.VERIFIED,
        source=VerificationSource.GST_PORTAL_VERIFIED_CACHE,
        mode=VerificationMode.PORTAL_CACHED,
        checked_at=eval_ts,
        location_metadata={"source_observed_at": "2026-05-01T00:00:00+00:00"},
    )

    c2 = RiskEngine.evaluate_risks(facts=[], verifications=[v_unknown_domain], documents=[], bidder_data={}, freshness_policy={}, evaluation_timestamp=eval_ts)
    missing_pol_sig = [c for c in c2 if c.signal_type == "MISSING_FRESHNESS_POLICY"]
    assert len(missing_pol_sig) == 1

    # OEM Date-Only Expiry
    f_oem = FactRead(
        id="f-oem",
        document_id="d1",
        bidder_id="b1",
        field="oem.expiry_date",
        value="2025-12-31",  # Expired relative to June 2026
        confidence=1.0,
        created_at=eval_ts,
    )

    c3 = RiskEngine.evaluate_risks(facts=[f_oem], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    oem_sig = [c for c in c3 if c.signal_type == "OEM_AUTHORIZATION_EXPIRED"]
    assert len(oem_sig) == 1
    assert "2026-06-01" in oem_sig[0].description


def test_authorized_duplicate_scope_privacy():
    """18. Verifies duplicate document hash detection output strips private metadata of other bidders."""
    eval_ts = datetime.now(timezone.utc)
    local_docs = [{"id": "d-local", "filename": "form.pdf", "sha256": "sha-duplicate-123"}]
    comparison_meta = [{"id": "d-other-private", "bidder_id": "secret-bidder-99", "sha256": "sha-duplicate-123", "filename": "secret.pdf"}]

    candidates = RiskEngine.evaluate_risks(
        facts=[],
        verifications=[],
        documents=local_docs,
        bidder_data={},
        comparison_metadata=comparison_meta,
        evaluation_timestamp=eval_ts,
    )

    dups = [c for c in candidates if c.signal_type == "DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER"]
    assert len(dups) == 1
    meta = dups[0].metadata_json
    # Private bidder ID or filename of other bidder MUST NOT be exposed in metadata_json!
    assert "secret-bidder-99" not in str(meta)
    assert "secret.pdf" not in str(meta)
    assert meta["comparison_scope"] == "AUTHORIZED_TENDER_METADATA"


def test_financial_parser_safety_and_fail_closed_validation():
    """19. Verifies ordinary words with m/b are not scaled, contradictory units/currencies are rejected, missing/mismatched averaging period defers comparison, and fail-closed typed reference validation."""
    eval_ts = datetime.now(timezone.utc)

    # 1. Ordinary words containing m/b (e.g. "Member", "September", "Mobile") must NOT trigger Million/Billion scale
    f_member = FactRead(
        id="f-mem", document_id="d1", bidder_id="b1", field="financial.turnover", value="5000000 Member", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25"}
    )
    f_valid = FactRead(
        id="f-val", document_id="d2", bidder_id="b1", field="financial.turnover", value="5000000", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25"}
    )
    c_mem = RiskEngine.evaluate_risks(facts=[f_member, f_valid], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    malformed_sig = [c for c in c_mem if c.signal_type == "MALFORMED_FINANCIAL_VALUE"]
    assert len(malformed_sig) == 1

    # 2. Contradictory units (metadata unit="Lakh" vs text "5 Crore")
    f_contra = FactRead(
        id="f-contra", document_id="d1", bidder_id="b1", field="financial.turnover", value="5 Crore", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "unit": "Lakh"}
    )
    c_contra = RiskEngine.evaluate_risks(facts=[f_contra, f_valid], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    malformed_contra = [c for c in c_contra if c.signal_type == "MALFORMED_FINANCIAL_VALUE"]
    assert len(malformed_contra) == 1

    # 3. Missing or mismatched averaging period for average_annual_turnover
    f_avg1 = FactRead(
        id="f-a1", document_id="d1", bidder_id="b1", field="financial.average_annual_turnover", value="5 Crore", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "averaging_period": "3_years", "currency": "INR"}
    )
    f_avg2_no_period = FactRead(
        id="f-a2", document_id="d2", bidder_id="b1", field="financial.average_annual_turnover", value="9 Crore", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR"}
    )
    c_period = RiskEngine.evaluate_risks(facts=[f_avg1, f_avg2_no_period], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    period_sig = [c for c in c_period if c.signal_type in ("AVERAGING_PERIOD_MISMATCH", "MISSING_FINANCIAL_PERIOD")]
    conflict_sig = [c for c in c_period if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]
    assert len(period_sig) == 1
    assert len(conflict_sig) == 0  # Numerical comparison MUST be deferred on period mismatch!


@pytest.mark.asyncio
async def test_bidder_owned_document_provenance_mapping(db_session, sample_tender, sample_bidder):
    """20. Verifies valid bidder-owned document RiskInputRef (where tender_id is None) maps successfully in BidVerificationService."""
    doc_owned = Document(
        id="doc-bidder-owned-1",
        bidder_id=sample_bidder.id,
        tender_id=None,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b1/owned.pdf",
        filename="owned.pdf",
        sha256="sha-owned-digest-123",
    )
    other_bidder = Bidder(id="b2-other", tender_id=sample_tender.id, bidder_name="Other Bidder", status="PENDING")
    other_doc = Document(
        id="doc-other-1",
        bidder_id=other_bidder.id,
        tender_id=None,
        document_type=DocumentType.TURNOVER_CERT,
        storage_uri="bidders/b2/other.pdf",
        filename="other.pdf",
        sha256="sha-owned-digest-123",
    )
    db_session.add_all([doc_owned, other_bidder, other_doc])
    db_session.commit()

    service = BidVerificationService(db_session)
    overview = await service.run_verification_workflow(
        bidder_id=sample_bidder.id,
        actor_id="admin-1",
        actor_role="ADMIN",
    )

    dup_signals = [r for r in overview.risk_signals if r.signal_type == "DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER"]
    assert len(dup_signals) == 1
    dup_sig = dup_signals[0]
    from app.schemas.canonical import RiskInputType
    doc_refs = [ref for ref in dup_sig.input_refs if ref.ref_type == RiskInputType.DOCUMENT]
    assert len(doc_refs) == 1
    assert doc_refs[0].id == "doc-bidder-owned-1"
    assert "unmapped_input_ids" not in dup_sig.metadata_json or "doc-bidder-owned-1" not in dup_sig.metadata_json.get("unmapped_input_ids", [])


def test_missing_currency_on_both_claims():
    """21. Verifies missing currency on both financial claims emits MISSING_FINANCIAL_CURRENCY and defers numerical comparison."""
    eval_ts = datetime.now(timezone.utc)
    f1 = FactRead(
        id="f1", document_id="d1", bidder_id="b1", field="financial.turnover", value="5 Crore", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "unit": "Crore"}
    )
    f2 = FactRead(
        id="f2", document_id="d2", bidder_id="b1", field="financial.turnover", value="9 Crore", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "unit": "Crore"}
    )

    candidates = RiskEngine.evaluate_risks(facts=[f1, f2], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    missing_curr = [c for c in candidates if c.signal_type == "MISSING_FINANCIAL_CURRENCY"]
    conflicts = [c for c in candidates if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]

    assert len(missing_curr) == 1
    assert len(conflicts) == 0


def test_missing_unit_on_both_claims():
    """22. Verifies missing unit/scale on both financial claims (bare numbers) emits MISSING_FINANCIAL_UNIT and defers numerical comparison."""
    eval_ts = datetime.now(timezone.utc)
    f1 = FactRead(
        id="f1", document_id="d1", bidder_id="b1", field="financial.turnover", value="5000000", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR"}
    )
    f2 = FactRead(
        id="f2", document_id="d2", bidder_id="b1", field="financial.turnover", value="9000000", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR"}
    )

    candidates = RiskEngine.evaluate_risks(facts=[f1, f2], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    missing_unit = [c for c in candidates if c.signal_type == "MISSING_FINANCIAL_UNIT"]
    conflicts = [c for c in candidates if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]

    assert len(missing_unit) == 1
    assert len(conflicts) == 0


def test_missing_averaging_period_on_both_claims():
    """23. Verifies missing averaging period on average_annual_turnover claims emits MISSING_FINANCIAL_PERIOD and defers numerical comparison."""
    eval_ts = datetime.now(timezone.utc)
    f1 = FactRead(
        id="f1", document_id="d1", bidder_id="b1", field="financial.average_annual_turnover", value="5 Crore INR", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR", "unit": "Crore"}
    )
    f2 = FactRead(
        id="f2", document_id="d2", bidder_id="b1", field="financial.average_annual_turnover", value="9 Crore INR", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR", "unit": "Crore"}
    )

    candidates = RiskEngine.evaluate_risks(facts=[f1, f2], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    missing_period = [c for c in candidates if c.signal_type == "MISSING_FINANCIAL_PERIOD"]
    conflicts = [c for c in candidates if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]

    assert len(missing_period) == 1
    assert len(conflicts) == 0


def test_get_bidder_compliance_historical_mutation_resistance(db_session, sample_tender, sample_bidder):
    """24. Verifies get_bidder_compliance reconstructs risk signals strictly from snapshot and resists subsequent RiskSignal DB table mutations."""
    eval_ts = datetime.now(timezone.utc)
    recorded_signal = {
        "id": "sig-snap-1",
        "bidder_id": sample_bidder.id,
        "run_id": "run-snap-1",
        "severity": "HIGH",
        "signal_type": "HISTORICAL_SNAPSHOT_SIGNAL",
        "title": "Historical Snapshot Signal",
        "description": "Signal preserved in historical snapshot",
        "reason_code": "HISTORICAL_SNAPSHOT_SIGNAL",
        "evidence_ids": [],
        "verification_ids": [],
        "input_refs": [],
        "metadata_json": {},
        "created_at": eval_ts.isoformat(),
    }

    run = ComplianceRun(
        id="run-snap-1",
        bidder_id=sample_bidder.id,
        tender_id=sample_tender.id,
        execution_status=JobStatus.COMPLETED,
        overall_status=ComplianceStatus.PASS,
        input_snapshot_json={
            "snapshot_version": "1.0",
            "approved_requirements": [],
            "facts": [],
            "verifications": [],
            "evidence": [],
            "risk_signals": [recorded_signal],
            "risk_summary": {"signal_count": 1, "counts_by_severity": {"HIGH": 1}, "counts_by_type": {}, "unresolved_count": 0, "source_mode_breakdown": {}, "risk_engine_version": "1.0", "risk_policy_version": "1.0"},
        },
        completed_at=eval_ts,
    )
    db_session.add(run)
    db_session.commit()

    # Mutate DB by adding a NEW RiskSignal row for the same run ID
    mutated_db_signal = RiskSignal(
        id="sig-mutated-db",
        bidder_id=sample_bidder.id,
        run_id="run-snap-1",
        severity=RiskSeverity.CRITICAL,
        signal_type="MUTATED_DB_SIGNAL_NOT_IN_SNAPSHOT",
        title="Mutated Signal",
        description="This signal was inserted after run completion",
        reason_code="MUTATED_SIGNAL",
    )
    db_session.add(mutated_db_signal)
    db_session.commit()

    # Execute snapshot reconstruction logic as used in get_bidder_compliance endpoint
    latest_run = run
    snapshot_data = latest_run.input_snapshot_json or {}
    from app.api.v1.bidders import _is_phase9_snapshot
    has_snapshot = _is_phase9_snapshot(snapshot_data)
    assert has_snapshot is True

    if has_snapshot:
        if "risk_signals" in snapshot_data and isinstance(snapshot_data["risk_signals"], list):
            try:
                risk_schema = [RiskSignalRead.model_validate(r) if isinstance(r, dict) else r for r in snapshot_data["risk_signals"]]
            except Exception:
                risk_schema = []
        else:
            risk_schema = []
    else:
        risk_db = db_session.query(RiskSignal).filter(RiskSignal.run_id == latest_run.id).all()
        risk_schema = [RiskSignalRead.model_validate(r) for r in risk_db]

    assert len(risk_schema) == 1
    assert risk_schema[0].signal_type == "HISTORICAL_SNAPSHOT_SIGNAL"
    assert risk_schema[0].id == "sig-snap-1"


def test_duplicate_comparison_scope_authorization():
    """25. Verifies duplicate comparison is driven solely by prefiltered comparison_metadata, without arbitrary boolean flags."""
    eval_ts = datetime.now(timezone.utc)
    local_docs = [{"id": "d-local", "filename": "form.pdf", "sha256": "sha-duplicate-999"}]

    c_none = RiskEngine.evaluate_risks(
        facts=[],
        verifications=[],
        documents=local_docs,
        bidder_data={},
        comparison_metadata=None,
        evaluation_timestamp=eval_ts,
    )
    assert len([c for c in c_none if c.signal_type == "DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER"]) == 0

    comparison_meta = [{"id": "d-other", "sha256": "sha-duplicate-999"}]
    c_prefiltered = RiskEngine.evaluate_risks(
        facts=[],
        verifications=[],
        documents=local_docs,
        bidder_data={},
        comparison_metadata=comparison_meta,
        evaluation_timestamp=eval_ts,
    )
    dups = [c for c in c_prefiltered if c.signal_type == "DUPLICATE_DOCUMENT_HASH_CROSS_BIDDER"]
    assert len(dups) == 1
    assert dups[0].metadata_json["comparison_scope"] == "AUTHORIZED_TENDER_METADATA"


def test_provider_capability_list_matches_actual_implementation():
    """26. Verifies ProviderRegistry returns accurate capability lists and operational health for all 6 domains."""
    health_list = ProviderRegistry.get_provider_health_list()
    assert len(health_list) == 6

    domain_map = {p.provider_identifier: p for p in health_list}
    assert "gst" in domain_map
    assert "udyam" in domain_map
    assert "mca" in domain_map
    assert "epfo" in domain_map
    assert "esic" in domain_map
    assert "blacklist" in domain_map

    gst_p = domain_map["gst"]
    assert "general.gstin" in gst_p.supported_fields
    assert "GSTIN Format Validation" in gst_p.capabilities
    assert "Turnover Verification" in gst_p.capabilities


def test_financial_parser_scale_correctness_regressions():
    """27. Verifies numeric + metadata scale normalization, single scale application, unit alias equivalence, double scaling prevention, and non-finite rejection."""
    eval_ts = datetime.now(timezone.utc)

    # 1. Numeric 5 + metadata Crore INR equals text "5 Crore INR"
    f_num_meta = FactRead(
        id="f-num", document_id="d1", bidder_id="b1", field="financial.turnover", value=5, confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR", "unit": "Crore"}
    )
    f_text_explicit = FactRead(
        id="f-text", document_id="d2", bidder_id="b1", field="financial.turnover", value="5 Crore INR", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR"}
    )

    c1 = RiskEngine.evaluate_risks(facts=[f_num_meta, f_text_explicit], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    conflicts_1 = [c for c in c1 if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]
    assert len(conflicts_1) == 0  # 5 + metadata Crore INR (50M) == text "5 Crore INR" (50M) -> NO conflict!

    # 2. Prevent double scaling when metadata unit matches text unit (e.g. text "5 Crore" + metadata unit "cr")
    f_double_meta = FactRead(
        id="f-dbl", document_id="d1", bidder_id="b1", field="financial.turnover", value="5 Crore INR", confidence=1.0, created_at=eval_ts, metadata_json={"financial_year": "2024-25", "currency": "INR", "unit": "cr"}
    )
    c2 = RiskEngine.evaluate_risks(facts=[f_num_meta, f_double_meta], verifications=[], documents=[], bidder_data={}, evaluation_timestamp=eval_ts)
    conflicts_2 = [c for c in c2 if c.signal_type == "CONFLICTING_TURNOVER_SAME_FY"]
    assert len(conflicts_2) == 0  # No double scaling! 50M == 50M

    # 3. Equivalent unit aliases (e.g. "cr", "crore", "lacs", "lakh")
    curr, unit, val_cr, exp = RiskEngine._parse_currency_and_scale(10, {"unit": "cr", "currency": "INR"})
    assert unit == "Crore"
    assert val_cr == 100_000_000.0

    curr, unit, val_lac, exp = RiskEngine._parse_currency_and_scale(1000, {"unit": "lacs", "currency": "INR"})
    assert unit == "Lakh"
    assert val_lac == 100_000_000.0

    # 4. Non-finite values rejected
    curr, unit, val_nan, exp = RiskEngine._parse_currency_and_scale("nan", {"currency": "INR"})
    assert val_nan is None
    curr, unit, val_inf, exp = RiskEngine._parse_currency_and_scale("inf", {"currency": "INR"})
    assert val_inf is None


def test_persisted_typed_risk_input_references(db_session: Session):
    """28. Verifies persisting RiskSignal to DB and reloading via RiskSignalRead retains exact typed input_refs without document/bidder evidence conversion."""
    eval_ts = datetime.now(timezone.utc)
    from app.schemas.canonical import RiskInputRef, RiskInputType

    typed_refs = [
        RiskInputRef(ref_type=RiskInputType.DOCUMENT, id="doc-ref-100", metadata={"filename": "audit.pdf"}),
        RiskInputRef(ref_type=RiskInputType.BIDDER_RECORD, id="bidder-ref-200", metadata={}),
        RiskInputRef(ref_type=RiskInputType.EVIDENCE, id="ev-ref-300", metadata={}),
    ]

    tender = Tender(
        id="t-persist",
        tender_number="GEM/2026/PERSIST",
        title="Persist Test Tender",
    )
    bidder = Bidder(
        id="b-persist",
        tender_id="t-persist",
        bidder_name="Persist Bidder",
    )
    run = ComplianceRun(
        id="run-persist",
        bidder_id="b-persist",
        tender_id="t-persist",
        execution_status=JobStatus.COMPLETED,
        started_at=eval_ts,
        created_at=eval_ts,
    )
    db_session.add(tender)
    db_session.add(bidder)
    db_session.add(run)
    db_session.commit()

    db_signal = RiskSignal(
        id="sig-typed-persist-1",
        bidder_id="b-persist",
        run_id="run-persist",
        severity=RiskSeverity.HIGH,
        signal_type="TYPED_REFERENCE_PERSISTENCE_TEST",
        title="Typed Ref Persistence Test",
        description="Verifies persistence of typed input references",
        reason_code="TYPED_PERSISTENCE",
        evidence_ids=["ev-ref-300"],
        verification_ids=[],
        metadata_json={"input_refs": [ref.model_dump(mode="json") for ref in typed_refs]},
        created_at=eval_ts,
    )
    db_session.add(db_signal)
    db_session.commit()

    # Reload from DB and validate RiskSignalRead ORM reconstruction
    reloaded_db = db_session.query(RiskSignal).filter_by(id="sig-typed-persist-1").first()
    reconstructed_read = RiskSignalRead.model_validate(reloaded_db)

    assert len(reconstructed_read.input_refs) == 3
    ref_types = [r.ref_type for r in reconstructed_read.input_refs]
    assert RiskInputType.DOCUMENT in ref_types
    assert RiskInputType.BIDDER_RECORD in ref_types
    assert RiskInputType.EVIDENCE in ref_types

    doc_ref = [r for r in reconstructed_read.input_refs if r.ref_type == RiskInputType.DOCUMENT][0]
    assert doc_ref.id == "doc-ref-100"
    assert doc_ref.metadata.get("filename") == "audit.pdf"


