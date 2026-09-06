from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.db.session import Base, SessionLocal, engine
from app.models.domain import (
    AuditEvent,
    Bidder,
    ComplianceRun,
    Document,
    ExtractedFact,
    HumanDecisionStatus,
    JobStatus,
    ProcessingJob,
    RiskSignal,
    RuleEvaluation,
    Tender,
    TenderRequirement,
    VerificationResult,
)
from app.schemas.canonical import (
    ComplianceStatus,
    DocumentType,
    OperatorEnum,
    RequirementType,
)
from app.services.bid_verification_service import BidVerificationService


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_setup(db: Session):
    """Sets up a test tender, bidder, document, and fact in DB."""
    tender = Tender(
        tender_number="TNT-HIST-001",
        title="History Test Tender",
        status=JobStatus.COMPLETED,
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)

    req = TenderRequirement(
        tender_id=tender.id,
        clause="1.1",
        requirement_type=RequirementType.GST,
        field="general.gstin",
        operator=OperatorEnum.EXISTS,
        expected_value=True,
        mandatory=True,
    )
    db.add(req)

    bidder = Bidder(
        tender_id=tender.id,
        bidder_name="History Corp",
        gstin="27ABCDE1234F1Z5",
        status=HumanDecisionStatus.PENDING,
        metadata_json={"verification_mode": "demo"},
    )
    db.add(bidder)
    db.commit()
    db.refresh(bidder)

    doc = Document(
        bidder_id=bidder.id,
        document_type=DocumentType.GST_CERT,
        storage_uri="s3://bucket/gst.pdf",
        filename="gst.pdf",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    fact = ExtractedFact(
        document_id=doc.id,
        bidder_id=bidder.id,
        field="general.gstin",
        value="27ABCDE1234F1Z5",
    )
    db.add(fact)
    db.commit()

    return {"tender": tender, "bidder": bidder, "doc": doc, "fact": fact}


@pytest.mark.asyncio
async def test_get_compliance_no_side_effects(db: Session, test_setup):
    client = TestClient(app)
    bidder = test_setup["bidder"]

    # 1. GET /compliance on bidder with no runs
    res = client.get(f"/api/v1/bidders/{bidder.id}/compliance")
    assert res.status_code == 200
    data = res.json()
    assert data["overall_status"] == "UNKNOWN"
    assert data["rule_evaluations"] == []
    assert data["risk_signals"] == []

    # 2. Verify no ComplianceRun was created by GET
    run_count = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).count()
    assert run_count == 0


@pytest.mark.asyncio
async def test_multiple_verifications_create_append_only_history(db: Session, test_setup):
    service = BidVerificationService(db)
    bidder = test_setup["bidder"]

    # Run 1
    res1 = await service.run_verification_workflow(bidder_id=bidder.id)
    runs_1 = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).all()
    assert len(runs_1) == 1
    run1 = runs_1[0]
    assert run1.execution_status == JobStatus.COMPLETED
    assert run1.overall_status is not None

    ver1 = db.query(VerificationResult).filter(VerificationResult.bidder_id == bidder.id).all()
    eval1 = db.query(RuleEvaluation).filter(RuleEvaluation.bidder_id == bidder.id).all()
    assert len(ver1) > 0
    assert len(eval1) > 0
    for v in ver1:
        assert v.run_id == run1.id
    for e in eval1:
        assert e.run_id == run1.id

    # Run 2
    res2 = await service.run_verification_workflow(bidder_id=bidder.id)
    runs_2 = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).order_by(ComplianceRun.created_at.asc()).all()
    assert len(runs_2) == 2
    run2 = runs_2[1]
    assert run1.id != run2.id

    # Verify run 1 data still exists intact (append-only)
    ver_run1 = db.query(VerificationResult).filter(VerificationResult.run_id == run1.id).all()
    ver_run2 = db.query(VerificationResult).filter(VerificationResult.run_id == run2.id).all()
    assert len(ver_run1) == len(ver1)
    assert len(ver_run2) > 0

    eval_run1 = db.query(RuleEvaluation).filter(RuleEvaluation.run_id == run1.id).all()
    eval_run2 = db.query(RuleEvaluation).filter(RuleEvaluation.run_id == run2.id).all()
    assert len(eval_run1) == len(eval1)
    assert len(eval_run2) > 0


@pytest.mark.asyncio
async def test_latest_run_semantics_ignores_running_or_failed(db: Session, test_setup):
    service = BidVerificationService(db)
    bidder = test_setup["bidder"]
    client = TestClient(app)

    # 1. Create a COMPLETED run (Run 1)
    res1 = await service.run_verification_workflow(bidder_id=bidder.id)
    completed_run = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).first()
    assert completed_run.execution_status == JobStatus.COMPLETED

    # 2. Manually insert a newer FAILED run (Run 2)
    failed_run = ComplianceRun(
        bidder_id=bidder.id,
        tender_id=test_setup["tender"].id,
        execution_status=JobStatus.FAILED,
        overall_status=ComplianceStatus.FAIL,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        summary_json={"error_code": "VERIFICATION_WORKFLOW_FAILED"},
    )
    db.add(failed_run)
    db.commit()

    # 3. Manually insert a newer RUNNING run (Run 3)
    running_run = ComplianceRun(
        bidder_id=bidder.id,
        tender_id=test_setup["tender"].id,
        execution_status=JobStatus.RUNNING,
        overall_status=None,
        started_at=datetime.now(timezone.utc),
        summary_json={},
    )
    db.add(running_run)
    db.commit()

    # GET /bidders/{id}/compliance must pick completed_run, ignoring FAILED and RUNNING runs
    res = client.get(f"/api/v1/bidders/{bidder.id}/compliance")
    assert res.status_code == 200
    data = res.json()
    assert len(data["rule_evaluations"]) > 0
    assert data["rule_evaluations"][0]["run_id"] == completed_run.id


@pytest.mark.asyncio
async def test_history_list_and_detail_endpoints(db: Session, test_setup):
    service = BidVerificationService(db)
    bidder = test_setup["bidder"]
    client = TestClient(app)

    # Execute workflow twice
    await service.run_verification_workflow(bidder_id=bidder.id)
    await service.run_verification_workflow(bidder_id=bidder.id)

    # GET /bidders/{bidder_id}/runs
    res_list = client.get(f"/api/v1/bidders/{bidder.id}/runs")
    assert res_list.status_code == 200
    runs = res_list.json()
    assert len(runs) == 2
    assert runs[0]["started_at"] >= runs[1]["started_at"]

    target_run_id = runs[0]["id"]

    # GET /bidders/{bidder_id}/runs/{run_id}
    res_detail = client.get(f"/api/v1/bidders/{bidder.id}/runs/{target_run_id}")
    assert res_detail.status_code == 200
    detail = res_detail.json()
    assert detail["run"]["id"] == target_run_id
    assert len(detail["verification_results"]) > 0
    assert len(detail["rule_evaluations"]) > 0


@pytest.mark.asyncio
async def test_cross_bidder_run_protection(db: Session, test_setup):
    service = BidVerificationService(db)
    bidder1 = test_setup["bidder"]
    client = TestClient(app)

    # Create run for bidder1
    await service.run_verification_workflow(bidder_id=bidder1.id)
    run1 = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder1.id).first()

    # Create bidder2
    bidder2 = Bidder(
        tender_id=test_setup["tender"].id,
        bidder_name="Other Corp",
        status=HumanDecisionStatus.PENDING,
    )
    db.add(bidder2)
    db.commit()

    # Attempt to request bidder1's run using bidder2's endpoint -> 404
    res_detail = client.get(f"/api/v1/bidders/{bidder2.id}/runs/{run1.id}")
    assert res_detail.status_code == 404

    # Attempt to request report for bidder2 with bidder1's run_id -> 404
    res_report = client.get(f"/api/v1/bidders/{bidder2.id}/report?run_id={run1.id}")
    assert res_report.status_code == 404


@pytest.mark.asyncio
async def test_failed_workflow_transaction_safety_and_error_sanitization(db: Session, test_setup, monkeypatch):
    service = BidVerificationService(db)
    bidder = test_setup["bidder"]

    # Force adapter failure during verification with secret-bearing error string
    async def mock_failed_verify(*args, **kwargs):
        raise RuntimeError("Simulated API Registry Collapse secret_token=gho_SECRET12345")

    monkeypatch.setattr(service.gst_adapter, "verify", mock_failed_verify)

    with pytest.raises(RuntimeError, match="Simulated API Registry Collapse"):
        await service.run_verification_workflow(bidder_id=bidder.id)

    # Verify run was preserved with execution_status = FAILED and safe error code
    run_db = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).first()
    assert run_db is not None
    assert run_db.execution_status == JobStatus.FAILED
    assert run_db.completed_at is not None
    assert run_db.summary_json.get("error_code") == "VERIFICATION_WORKFLOW_FAILED"

    # Assert raw exception text and secrets are NOT persisted in summary_json or audit events
    summary_str = str(run_db.summary_json)
    assert "Simulated API Registry Collapse" not in summary_str
    assert "secret_token" not in summary_str

    audit_events = db.query(AuditEvent).filter(AuditEvent.entity_id == bidder.id).all()
    failed_audits = [a for a in audit_events if a.action == "VERIFICATION_FAILED"]
    assert len(failed_audits) > 0
    audit_payload = str(failed_audits[0].payload_json)
    assert "Simulated API Registry Collapse" not in audit_payload
    assert "secret_token" not in audit_payload
    assert failed_audits[0].payload_json.get("error_code") == "VERIFICATION_WORKFLOW_FAILED"
    assert failed_audits[0].payload_json.get("error_type") == "RuntimeError"


@pytest.mark.asyncio
async def test_service_level_duplicate_run_protection(db: Session, test_setup):
    service = BidVerificationService(db)
    bidder = test_setup["bidder"]

    # Manually create an active RUNNING run with an active job
    job = ProcessingJob(
        target_type="BIDDER",
        target_id=bidder.id,
        job_type="VERIFY_BIDDER",
        status=JobStatus.RUNNING,
        current_stage="VERIFICATION",
        progress=20,
    )
    db.add(job)
    db.commit()

    running_run = ComplianceRun(
        bidder_id=bidder.id,
        tender_id=test_setup["tender"].id,
        job_id=job.id,
        execution_status=JobStatus.RUNNING,
        overall_status=None,
        started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        summary_json={},
    )
    db.add(running_run)
    db.commit()

    # Invoke run_verification_workflow while active RUNNING run exists
    res = await service.run_verification_workflow(bidder_id=bidder.id, job_id=job.id)
    assert res is not None

    # Confirm no duplicate RUNNING run was spawned
    runs = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).all()
    assert len(runs) == 1
    assert runs[0].id == running_run.id


@pytest.mark.asyncio
async def test_review_required_compliance_execution_status(db: Session, test_setup):
    service = BidVerificationService(db)
    bidder = test_setup["bidder"]

    # Perform workflow
    await service.run_verification_workflow(bidder_id=bidder.id)

    # Manually set overall_status to REVIEW_REQUIRED
    run = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).first()
    run.overall_status = ComplianceStatus.REVIEW_REQUIRED
    run.execution_status = JobStatus.COMPLETED
    db.commit()

    # Verify execution_status remains COMPLETED while overall_status is REVIEW_REQUIRED
    db.refresh(run)
    assert run.execution_status == JobStatus.COMPLETED
    assert run.overall_status == ComplianceStatus.REVIEW_REQUIRED


@pytest.mark.asyncio
async def test_historical_evaluation_evidence_endpoint_resolution(db: Session, test_setup):
    client = TestClient(app)
    service = BidVerificationService(db)
    bidder = test_setup["bidder"]

    # Run 1
    await service.run_verification_workflow(bidder_id=bidder.id)
    run1 = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).first()
    eval1 = db.query(RuleEvaluation).filter(RuleEvaluation.run_id == run1.id).first()
    assert eval1 is not None
    assert len(eval1.evidence_ids) > 0

    # Run 2
    await service.run_verification_workflow(bidder_id=bidder.id)
    runs = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).all()
    assert len(runs) == 2

    # Call API GET /api/v1/evaluations/{run1_eval_id}/evidence for historical Run 1 evaluation
    res = client.get(f"/api/v1/evaluations/{eval1.id}/evidence")
    assert res.status_code == 200
    evidence_list = res.json()
    assert isinstance(evidence_list, list)
    assert len(evidence_list) > 0


@pytest.mark.asyncio
async def test_risk_signal_run_id_scoping_and_history(db: Session, test_setup):
    service = BidVerificationService(db)
    bidder = test_setup["bidder"]

    # Add a mandatory requirement that deterministically FAILS to generate a RiskSignal
    req_fail = TenderRequirement(
        tender_id=test_setup["tender"].id,
        clause="2.1",
        requirement_type=RequirementType.TURNOVER,
        field="financial.average_annual_turnover",
        operator=OperatorEnum.GTE,
        expected_value=999999999999,
        mandatory=True,
    )
    db.add(req_fail)
    db.commit()

    # Run 1
    await service.run_verification_workflow(bidder_id=bidder.id)
    runs_1 = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).all()
    run1 = runs_1[0]

    risks_run1 = db.query(RiskSignal).filter(RiskSignal.run_id == run1.id).all()
    assert len(risks_run1) > 0
    for r in risks_run1:
        assert r.run_id == run1.id

    # Run 2
    await service.run_verification_workflow(bidder_id=bidder.id)
    runs_2 = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).order_by(ComplianceRun.created_at.asc()).all()
    run2 = runs_2[1]

    risks_run1_after = db.query(RiskSignal).filter(RiskSignal.run_id == run1.id).all()
    risks_run2_after = db.query(RiskSignal).filter(RiskSignal.run_id == run2.id).all()

    # Confirm Run 1 risk signals still exist and Run 2 received separate risk signals
    assert len(risks_run1_after) == len(risks_run1)
    assert len(risks_run2_after) > 0
    assert risks_run1_after[0].id != risks_run2_after[0].id


@pytest.mark.asyncio
async def test_api_duplicate_run_and_orphan_job_prevention(db: Session, test_setup):
    client = TestClient(app)
    bidder = test_setup["bidder"]

    # Manually insert a stale RUNNING ComplianceRun with no active job
    stale_run = ComplianceRun(
        bidder_id=bidder.id,
        tender_id=test_setup["tender"].id,
        job_id=None,
        execution_status=JobStatus.RUNNING,
        overall_status=None,
        started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        summary_json={},
    )
    db.add(stale_run)
    db.commit()

    # Call POST /api/v1/bidders/{id}/verify
    res = client.post(f"/api/v1/bidders/{bidder.id}/verify")
    assert res.status_code == 200
    job_data = res.json()
    assert job_data["job_type"] == "VERIFY_BIDDER"

    # Confirm stale run was marked FAILED with ORPHANED_ACTIVE_RUN
    db.refresh(stale_run)
    assert stale_run.execution_status == JobStatus.FAILED
    assert stale_run.summary_json.get("error_code") == "ORPHANED_ACTIVE_RUN"

    # Confirm a single new active job and completed run were created cleanly
    jobs = db.query(ProcessingJob).filter(ProcessingJob.target_id == bidder.id).all()
    assert len(jobs) == 1
    assert jobs[0].id == job_data["id"]

    runs = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).all()
    assert len(runs) == 2  # 1 stale FAILED + 1 new COMPLETED
    new_run = [r for r in runs if r.id != stale_run.id][0]
    assert new_run.execution_status == JobStatus.COMPLETED
    assert new_run.job_id == job_data["id"]
