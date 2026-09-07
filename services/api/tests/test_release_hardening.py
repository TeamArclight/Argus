import asyncio
import os
import uuid
from unittest.mock import patch
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import Base, SessionLocal, engine, get_db
from app.main import app
from app.models.domain import ActiveOperationLock, Bidder, ProcessingJob, Tender, TenderRequirement
from app.schemas.canonical import JobStage, JobStatus, RequirementType, UserRole
from app.services.idempotency_service import IdempotencyService
from app.services.operation_lock_service import OperationLockService
from tests.auth_helpers import get_auth_headers

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_request_id_correlation_and_validation():
    # 1. Automatic UUID generation when header absent
    res = client.get("/health")
    assert res.status_code == 200
    assert "X-Request-ID" in res.headers
    req_id = res.headers["X-Request-ID"]
    assert len(req_id) > 10

    # 2. Valid custom header accepted and echoed
    custom_id = "test-custom-request-id-12345"
    res2 = client.get("/health", headers={"X-Request-ID": custom_id})
    assert res2.status_code == 200
    assert res2.headers.get("X-Request-ID") == custom_id

    # 3. Malicious / invalid header sanitized to UUID
    bad_id = "<script>alert(1)</script>" + "A" * 100
    res3 = client.get("/health", headers={"X-Request-ID": bad_id})
    assert res3.status_code == 200
    assert res3.headers.get("X-Request-ID") != bad_id


def test_public_error_envelope_structure():
    # 404 Not Found error envelope
    res = client.get("/api/v1/tenders/non-existent-id-9999", headers=get_auth_headers())
    assert res.status_code == 404
    data = res.json()
    assert "error" in data
    err = data["error"]
    assert "code" in err
    assert "message" in err
    assert "request_id" in err
    assert "details" in err
    assert res.headers.get("X-Request-ID") == err["request_id"]

    # 401 Unauthenticated error envelope
    res_401 = client.get("/api/v1/tenders")
    assert res_401.status_code == 401
    assert "error" in res_401.json()

    # 422 Validation error envelope
    res_422 = client.post("/api/v1/tenders", json={"invalid_key": "data"}, headers=get_auth_headers())
    assert res_422.status_code == 422
    assert res_422.json()["error"]["code"] == "VALIDATION_ERROR"


def test_health_readiness_probe():
    res = client.get("/health/readiness")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert data["components"]["database"] == "connected"
    assert data["components"]["storage"] == "writable"
    assert "X-Request-ID" in res.headers


def test_protected_metrics_endpoint():
    # Unauthenticated -> 401
    res_anon = client.get("/api/v1/metrics")
    assert res_anon.status_code == 401

    # Non-admin role (REVIEWER) -> 403
    res_reviewer = client.get("/api/v1/metrics", headers=get_auth_headers(role=UserRole.REVIEWER))
    assert res_reviewer.status_code == 403

    # Admin role -> 200 OK
    res_admin = client.get("/api/v1/metrics", headers=get_auth_headers(role=UserRole.ADMIN))
    assert res_admin.status_code == 200
    data = res_admin.json()
    assert "uptime_seconds" in data
    assert "requests_total" in data


def test_idempotency_key_deduplication():
    # Create test tender
    create_res = client.post(
        "/api/v1/tenders",
        json={"tender_number": "TENDER-IDEM-001", "title": "Idempotency Test Tender"},
        headers=get_auth_headers(),
    )
    assert create_res.status_code == 201
    tender_id = create_res.json()["id"]

    # Create test bidder
    bidder_res = client.post(
        f"/api/v1/tenders/{tender_id}/bidders",
        json={"bidder_name": "Idempotent Bidder LLC"},
        headers=get_auth_headers(),
    )
    assert bidder_res.status_code == 201
    bidder_id = bidder_res.json()["id"]

    idem_key = "idem-key-test-uuid-999"
    headers = get_auth_headers()
    headers["X-Idempotency-Key"] = idem_key

    # Initial request
    res1 = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers)
    assert res1.status_code == 200
    job1_id = res1.json()["id"]

    # Second request with SAME idempotency key -> cached 200 OK response
    res2 = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers)
    assert res2.status_code == 200
    assert res2.json()["id"] == job1_id

    # Third request with SAME key but DIFFERENT endpoint/payload -> 409 Conflict
    headers_conflict = get_auth_headers()
    headers_conflict["X-Idempotency-Key"] = idem_key
    res_conflict = client.post(f"/api/v1/tenders/{tender_id}/process", headers=headers_conflict)
    assert res_conflict.status_code == 409
    assert res_conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_COLLISION"


def test_idempotency_key_validation_errors():
    create_res = client.post(
        "/api/v1/tenders",
        json={"tender_number": "TENDER-VAL-001", "title": "Validation Tender"},
        headers=get_auth_headers(),
    )
    tender_id = create_res.json()["id"]
    bidder_res = client.post(
        f"/api/v1/tenders/{tender_id}/bidders",
        json={"bidder_name": "Val Bidder"},
        headers=get_auth_headers(),
    )
    bidder_id = bidder_res.json()["id"]

    # Key too long (> 128 chars)
    headers = get_auth_headers()
    headers["X-Idempotency-Key"] = "a" * 129
    res = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"

    # Key with illegal characters (spaces or symbols)
    headers["X-Idempotency-Key"] = "invalid key with spaces"
    res = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"

    headers["X-Idempotency-Key"] = "bad<script>#key"
    res = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_idempotency_cross_resource_collision():
    create_res = client.post(
        "/api/v1/tenders",
        json={"tender_number": "TENDER-CROSS-001", "title": "Cross Resource Tender"},
        headers=get_auth_headers(),
    )
    tender_id = create_res.json()["id"]

    b1_res = client.post(
        f"/api/v1/tenders/{tender_id}/bidders",
        json={"bidder_name": "Bidder One"},
        headers=get_auth_headers(),
    )
    b1_id = b1_res.json()["id"]

    b2_res = client.post(
        f"/api/v1/tenders/{tender_id}/bidders",
        json={"bidder_name": "Bidder Two"},
        headers=get_auth_headers(),
    )
    b2_id = b2_res.json()["id"]

    shared_key = "shared-idem-key-12345"
    h1 = get_auth_headers()
    h1["X-Idempotency-Key"] = shared_key
    res1 = client.post(f"/api/v1/bidders/{b1_id}/verify", headers=h1)
    assert res1.status_code == 200

    # Same key used on different bidder -> 409 Collision
    h2 = get_auth_headers()
    h2["X-Idempotency-Key"] = shared_key
    res2 = client.post(f"/api/v1/bidders/{b2_id}/verify", headers=h2)
    assert res2.status_code == 409
    assert res2.json()["error"]["code"] == "IDEMPOTENCY_KEY_COLLISION"


def test_idempotency_in_progress_and_recovery():
    create_res = client.post(
        "/api/v1/tenders",
        json={"tender_number": "TENDER-RECOV-001", "title": "Recovery Tender"},
        headers=get_auth_headers(),
    )
    tender_id = create_res.json()["id"]
    bidder_res = client.post(
        f"/api/v1/tenders/{tender_id}/bidders",
        json={"bidder_name": "Recovery Bidder"},
        headers=get_auth_headers(),
    )
    bidder_id = bidder_res.json()["id"]

    idem_key = "recovery-idem-key-777"
    headers = get_auth_headers()
    headers["X-Idempotency-Key"] = idem_key

    # 1. Simulate in-progress state by injecting a running job into IdempotencyRecord
    with SessionLocal() as db:
        job = ProcessingJob(
            target_type="BIDDER",
            target_id=bidder_id,
            job_type="VERIFICATION",
            status=JobStatus.RUNNING,
            current_stage=JobStage.VERIFICATION,
            progress=50,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

        record = IdempotencyService.get_record(
            db=db,
            principal_id="test-user-001",
            resource_type="BIDDER",
            resource_id=bidder_id,
            operation="VERIFY_BIDDER",
            key=idem_key,
        )
        if not record:
            from app.models.domain import IdempotencyRecord
            record = IdempotencyRecord(
                key=idem_key,
                principal_id="test-user-001",
                resource_type="BIDDER",
                resource_id=bidder_id,
                operation="VERIFY_BIDDER",
                request_hash=IdempotencyService._compute_hash("test-user-001", "BIDDER", bidder_id, "VERIFY_BIDDER", None),
                status="PROCESSING",
                job_id=job_id,
            )
            db.add(record)
            db.commit()

    # Request while job is RUNNING -> 409 OPERATION_IN_PROGRESS
    res_in_prog = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers)
    assert res_in_prog.status_code == 409
    assert res_in_prog.json()["error"]["code"] == "OPERATION_IN_PROGRESS"

    # 2. Mark the job as FAILED to simulate failure / crash
    with SessionLocal() as db:
        failed_job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
        failed_job.status = JobStatus.FAILED
        failed_job.error_message = "Simulated worker crash"
        db.commit()

    # Retry with SAME idempotency key on a definitively failed operation -> rejected as 409 OPERATION_FAILED
    res_failed = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers)
    assert res_failed.status_code == 409
    assert res_failed.json()["error"]["code"] == "OPERATION_FAILED"

    # Explicit new attempt with NEW idempotency key -> creates new job successfully
    headers_new = get_auth_headers()
    headers_new["X-Idempotency-Key"] = "fresh-recovery-attempt-key-888"
    res_recovered = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers_new)
    assert res_recovered.status_code == 200
    new_job_id = res_recovered.json()["id"]
    assert new_job_id != job_id


def test_active_operation_resource_locking_concurrent_keys():
    create_res = client.post(
        "/api/v1/tenders",
        json={"tender_number": "TENDER-LOCK-001", "title": "Resource Lock Tender"},
        headers=get_auth_headers(),
    )
    tender_id = create_res.json()["id"]
    bidder_res = client.post(
        f"/api/v1/tenders/{tender_id}/bidders",
        json={"bidder_name": "Resource Lock Bidder"},
        headers=get_auth_headers(),
    )
    bidder_id = bidder_res.json()["id"]

    # Simulate an active operation lock held on the bidder by key A
    with SessionLocal() as db:
        from app.models.domain import ActiveOperationLock
        running_job = ProcessingJob(
            target_type="BIDDER",
            target_id=bidder_id,
            job_type="VERIFY_BIDDER",
            status=JobStatus.RUNNING,
            current_stage=JobStage.VERIFICATION,
            progress=30,
        )
        db.add(running_job)
        db.commit()
        db.refresh(running_job)

        active_lock = ActiveOperationLock(
            resource_type="BIDDER",
            resource_id=bidder_id,
            operation="VERIFY_BIDDER",
            job_id=running_job.id,
            owner_principal_id="test-user-001",
        )
        db.add(active_lock)
        db.commit()
        job_id = running_job.id

    # Second request with a completely DIFFERENT key B for the SAME bidder -> rejected by resource lock as 409
    h_b = get_auth_headers()
    h_b["X-Idempotency-Key"] = "completely-different-key-B"
    res_blocked = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=h_b)
    assert res_blocked.status_code == 409
    assert res_blocked.json()["error"]["code"] == "OPERATION_IN_PROGRESS"

    # Once the first job and lock finish, subsequent request succeeds
    with SessionLocal() as db:
        from app.models.domain import ActiveOperationLock
        db.query(ActiveOperationLock).filter(ActiveOperationLock.resource_id == bidder_id).delete()
        fin_job = db.query(ProcessingJob).filter(ProcessingJob.id == job_id).first()
        fin_job.status = JobStatus.COMPLETED
        db.commit()

    res_next = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=h_b)
    assert res_next.status_code == 200


def test_job_event_service_monotonic_allocation_and_uniqueness():
    from app.services.job_event_service import JobEventService
    from app.models.domain import JobEvent
    from sqlalchemy.exc import IntegrityError

    with SessionLocal() as db:
        job = ProcessingJob(
            target_type="BIDDER",
            target_id="test-seq-bidder",
            job_type="VERIFICATION",
            status=JobStatus.RUNNING,
            current_stage=JobStage.VERIFICATION,
            progress=10,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

        ev1 = JobEventService.emit_event(db, job_id, JobStage.EXTRACTION, JobStatus.RUNNING, 20, "Extract 1")
        ev2 = JobEventService.emit_event(db, job_id, JobStage.VERIFICATION, JobStatus.RUNNING, 50, "Verify 1")
        ev3 = JobEventService.emit_event(db, job_id, JobStage.REPORTING, JobStatus.COMPLETED, 100, "Done")
        db.commit()

        assert ev1.seq == 1
        assert ev2.seq == 2
        assert ev3.seq == 3

        # Direct duplicate seq insert must fail uniqueness constraint
        duplicate_ev = JobEvent(
            seq=1,
            job_id=job_id,
            stage=JobStage.EXTRACTION.value,
            status=JobStatus.RUNNING.value,
            progress=20,
            message="Duplicate seq test",
            payload={},
        )
        db.add(duplicate_ev)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_sse_authorization_enforcement():
    with SessionLocal() as db:
        tender = Tender(tender_number="TENDER-AUTH-SSE-01", title="SSE Auth Tender")
        db.add(tender)
        db.commit()
        db.refresh(tender)

        bidder1 = Bidder(tender_id=tender.id, bidder_name="Bidder One")
        db.add(bidder1)
        db.commit()
        db.refresh(bidder1)

        job_bidder = ProcessingJob(
            target_type="BIDDER",
            target_id=bidder1.id,
            job_type="VERIFY_BIDDER",
            status=JobStatus.COMPLETED,
            current_stage=JobStage.REPORTING,
            progress=100,
        )
        job_tender = ProcessingJob(
            target_type="TENDER",
            target_id=tender.id,
            job_type="EXTRACT_REQUIREMENTS",
            status=JobStatus.COMPLETED,
            current_stage=JobStage.EXTRACTION,
            progress=100,
        )
        db.add_all([job_bidder, job_tender])
        db.commit()
        db.refresh(job_bidder)
        db.refresh(job_tender)

    # 1. Unauthenticated request -> 401
    res_anon = client.get(f"/api/v1/jobs/{job_bidder.id}/events")
    assert res_anon.status_code == 401

    # 2. Invalid bearer token -> 401
    res_invalid = client.get(f"/api/v1/jobs/{job_bidder.id}/events", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert res_invalid.status_code == 401

    # 3. Procurement officer streaming job -> 200 OK
    res_officer = client.get(f"/api/v1/jobs/{job_bidder.id}/events", headers=get_auth_headers(role=UserRole.PROCUREMENT_OFFICER))
    assert res_officer.status_code == 200

    # 4. Admin streaming job -> 200 OK
    res_admin = client.get(f"/api/v1/jobs/{job_bidder.id}/events", headers=get_auth_headers(role=UserRole.ADMIN))
    assert res_admin.status_code == 200


def test_sse_cursor_validation():
    with SessionLocal() as db:
        job = ProcessingJob(
            target_type="BIDDER",
            target_id="test-cursor-bidder",
            job_type="VERIFICATION",
            status=JobStatus.COMPLETED,
            current_stage=JobStage.REPORTING,
            progress=100,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

    # Invalid cursor format (non-integer string) -> 422
    res_invalid = client.get(
        f"/api/v1/jobs/{job_id}/events",
        headers={**get_auth_headers(), "Last-Event-ID": "invalid-cursor"},
    )
    assert res_invalid.status_code == 422
    assert res_invalid.json()["error"]["code"] == "INVALID_EVENT_CURSOR"


def test_sse_streaming_monotonic_seq_and_resume():
    from app.models.domain import JobEvent

    # Create job and populate sequential events
    with SessionLocal() as db:
        job = ProcessingJob(
            target_type="BIDDER",
            target_id="test-bidder-sse",
            job_type="VERIFICATION",
            status=JobStatus.COMPLETED,
            current_stage=JobStage.REPORTING,
            progress=100,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

        ev1 = JobEvent(
            seq=1,
            job_id=job_id,
            stage=JobStage.EXTRACTION,
            status=JobStatus.RUNNING,
            progress=25,
            message="Extracted entity details",
            payload={"doc_count": 2},
        )
        ev2 = JobEvent(
            seq=2,
            job_id=job_id,
            stage=JobStage.VERIFICATION,
            status=JobStatus.RUNNING,
            progress=60,
            message="Verified PAN and GSTIN",
            payload={"checks": 2},
        )
        ev3 = JobEvent(
            seq=3,
            job_id=job_id,
            stage=JobStage.REPORTING,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Verification complete",
            payload={"summary": "ok"},
        )
        db.add_all([ev1, ev2, ev3])
        db.commit()

    # 1. Full stream from beginning
    res = client.get(f"/api/v1/jobs/{job_id}/events", headers=get_auth_headers())
    assert res.status_code == 200
    content = res.text
    assert "id: 1" in content
    assert "id: 2" in content
    assert "id: 3" in content
    assert "event: job_terminal" in content

    # 2. Resumption with Last-Event-ID: 2
    res_resumed = client.get(
        f"/api/v1/jobs/{job_id}/events",
        headers={**get_auth_headers(), "Last-Event-ID": "2"},
    )
    assert res_resumed.status_code == 200
    resumed_content = res_resumed.text
    assert "id: 1" not in resumed_content
    assert "id: 2" not in resumed_content
    assert "id: 3" in resumed_content
    assert "event: job_terminal" in resumed_content


def test_sse_event_sanitization():
    from app.models.domain import JobEvent

    with SessionLocal() as db:
        job = ProcessingJob(
            target_type="BIDDER",
            target_id="test-bidder-sanitization",
            job_type="VERIFICATION",
            status=JobStatus.FAILED,
            current_stage=JobStage.VERIFICATION,
            progress=50,
            error_message="Failed due to db connection postgresql://user:supersecret@db:5432/main",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

        ev = JobEvent(
            seq=1,
            job_id=job_id,
            stage=JobStage.VERIFICATION,
            status=JobStatus.FAILED,
            progress=50,
            message="API call failed with api_key: AIzaSyD987654321 and password=supersecretpass",
            payload={"api_token": "secret-12345", "db_uri": "sqlite:///argus.db"},
        )
        db.add(ev)
        db.commit()

    res = client.get(f"/api/v1/jobs/{job_id}/events", headers=get_auth_headers())
    assert res.status_code == 200
    content = res.text
    assert "supersecret" not in content
    assert "AIzaSy" not in content
    assert "[REDACTED]" in content


def test_ambiguous_lock_recovery_fails_closed():
    """Verifies that an orphaned lock with missing linked job fails closed and is NOT silently deleted."""
    res_id = f"b-ambig-unit-{uuid.uuid4()}"
    with SessionLocal() as db:
        orphan_lock = ActiveOperationLock(
            resource_type="BIDDER",
            resource_id=res_id,
            operation="VERIFY_BIDDER",
            job_id="missing-job-unit-999",
            owner_principal_id="test-user-001",
        )
        db.add(orphan_lock)
        db.commit()

        # Attempt to acquire lock for a new job
        with pytest.raises(HTTPException) as exc_info:
            OperationLockService.acquire_lock(
                db=db,
                resource_type="BIDDER",
                resource_id=res_id,
                operation="VERIFY_BIDDER",
                job_id="new-job-unit-111",
                principal_id="test-user-002",
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "OPERATION_LOCK_RECOVERY_REQUIRED"

        # Verify orphan lock was NOT silently deleted
        remaining_lock = db.query(ActiveOperationLock).filter_by(resource_id=res_id).first()
        assert remaining_lock is not None
        assert remaining_lock.job_id == "missing-job-unit-999"

        # Clean up
        db.delete(remaining_lock)
        db.commit()


def test_scoped_lock_release_enforcement():
    """Verifies that release_lock requires job_id, rejects wrong job IDs, and releases only its own lock."""
    res_id_1 = f"b-scoped-1-{uuid.uuid4()}"
    res_id_2 = f"b-scoped-2-{uuid.uuid4()}"
    job_id_1 = f"job-scoped-1-{uuid.uuid4()}"
    job_id_2 = f"job-scoped-2-{uuid.uuid4()}"

    with SessionLocal() as db:
        # Create two distinct active locks
        lock1 = ActiveOperationLock(
            resource_type="BIDDER",
            resource_id=res_id_1,
            operation="VERIFY_BIDDER",
            job_id=job_id_1,
            owner_principal_id="user-1",
        )
        lock2 = ActiveOperationLock(
            resource_type="BIDDER",
            resource_id=res_id_2,
            operation="VERIFY_BIDDER",
            job_id=job_id_2,
            owner_principal_id="user-2",
        )
        db.add(lock1)
        db.add(lock2)
        db.commit()

        # 1. Missing or empty job_id is rejected with ValueError
        with pytest.raises(ValueError):
            OperationLockService.release_lock(db, "BIDDER", res_id_1, "VERIFY_BIDDER", job_id="")

        with pytest.raises(ValueError):
            OperationLockService.release_lock(db, "BIDDER", res_id_1, "VERIFY_BIDDER", job_id="   ")

        # 2. Wrong job_id cannot release lock
        released_wrong = OperationLockService.release_lock(
            db, "BIDDER", res_id_1, "VERIFY_BIDDER", job_id="wrong-job-id-999"
        )
        assert released_wrong is False
        assert db.query(ActiveOperationLock).filter_by(resource_id=res_id_1).first() is not None

        # 3. Correct job_id releases only its own lock
        released_correct = OperationLockService.release_lock(
            db, "BIDDER", res_id_1, "VERIFY_BIDDER", job_id=job_id_1
        )
        assert released_correct is True
        assert db.query(ActiveOperationLock).filter_by(resource_id=res_id_1).first() is None
        # Other lock remains intact
        assert db.query(ActiveOperationLock).filter_by(resource_id=res_id_2).first() is not None

        # Clean up second lock
        released_2 = OperationLockService.release_lock(
            db, "BIDDER", res_id_2, "VERIFY_BIDDER", job_id=job_id_2
        )
        assert released_2 is True
        assert db.query(ActiveOperationLock).filter_by(resource_id=res_id_2).first() is None



