import asyncio
import os
from unittest.mock import patch
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.session import Base, engine, get_db
from app.main import app
from app.models.domain import Bidder, ProcessingJob, Tender, TenderRequirement
from app.schemas.canonical import JobStage, JobStatus, RequirementType, UserRole
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


def test_idempotency_invalid_key_format_422():
    # First create tender & bidder so endpoint reaches idempotency validation
    create_res = client.post(
        "/api/v1/tenders",
        json={"tender_number": "TENDER-IDEM-422", "title": "Idempotency 422 Test Tender"},
        headers=get_auth_headers(),
    )
    assert create_res.status_code == 201
    tender_id = create_res.json()["id"]

    bidder_res = client.post(
        f"/api/v1/tenders/{tender_id}/bidders",
        json={"bidder_name": "Idempotent 422 Bidder LLC"},
        headers=get_auth_headers(),
    )
    assert bidder_res.status_code == 201
    bidder_id = bidder_res.json()["id"]

    headers = get_auth_headers()
    headers["X-Idempotency-Key"] = "invalid key with spaces!@#"
    res = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_idempotency_crash_retry_recovery():
    from app.db.session import SessionLocal
    from app.models.domain import IdempotencyRecord, ProcessingJob
    from datetime import datetime, timedelta, timezone

    db = SessionLocal()
    try:
        # Create a crashed job in status FAILED
        crashed_job = ProcessingJob(
            target_type="BIDDER",
            target_id="bidder-crashed-123",
            job_type="VERIFY_BIDDER",
            status=JobStatus.FAILED,
            error_message="Worker crashed unexpectedly",
        )
        db.add(crashed_job)
        db.commit()

        # Create an idempotency record referencing the crashed job
        idem_rec = IdempotencyRecord(
            key="idem-key-crashed-run",
            principal_id="test-user-id-12345",
            resource_type="BIDDER",
            resource_id="bidder-crashed-123",
            operation="VERIFY_BIDDER",
            request_hash="dummy-hash",
            status="PROCESSING",
            job_id=crashed_job.id,
        )
        db.add(idem_rec)
        db.commit()
    finally:
        db.close()

    # Retry request with same key should recover crashed state rather than 409 conflict
    create_tender = client.post(
        "/api/v1/tenders",
        json={"tender_number": "TENDER-CRASH-001", "title": "Crash Recovery Test Tender"},
        headers=get_auth_headers(),
    )
    tender_id = create_tender.json()["id"]

    # Re-use exact bidder ID
    db = SessionLocal()
    try:
        bidder = Bidder(id="bidder-crashed-123", tender_id=tender_id, bidder_name="Crashed Bidder LLC")
        db.add(bidder)
        db.commit()
    finally:
        db.close()

    headers = get_auth_headers()
    headers["X-Idempotency-Key"] = "idem-key-crashed-run"
    res = client.post("/api/v1/bidders/bidder-crashed-123/verify", headers=headers)
    # Should proceed cleanly without 409 Conflict
    assert res.status_code in (200, 201)


def test_sse_monotonic_integer_cursor_and_reconnect():
    from app.db.session import SessionLocal
    from app.models.domain import JobEvent, ProcessingJob

    job_id = "job-sse-test-100"
    db = SessionLocal()
    try:
        job = ProcessingJob(
            id=job_id,
            target_type="BIDDER",
            target_id="bidder-sse-100",
            job_type="VERIFY_BIDDER",
            status=JobStatus.RUNNING,
        )
        db.add(job)
        db.commit()

        ev1 = JobEvent(job_id=job_id, seq=1, stage=JobStage.VERIFICATION, status=JobStatus.RUNNING, progress=10, message="Stage 1 started")
        ev2 = JobEvent(job_id=job_id, seq=2, stage=JobStage.VERIFICATION, status=JobStatus.RUNNING, progress=50, message="Stage 2 running")
        ev3 = JobEvent(job_id=job_id, seq=3, stage=JobStage.VERIFICATION, status=JobStatus.RUNNING, progress=90, message="Stage 3 finishing")
        db.add_all([ev1, ev2, ev3])
        db.commit()
    finally:
        db.close()

    # Reconnect with Last-Event-ID: 2 -> should receive only event 3
    headers = get_auth_headers()
    headers["Last-Event-ID"] = "2"
    res = client.get(f"/api/v1/jobs/{job_id}/events", headers=headers)
    assert res.status_code == 200
    content = res.text
    assert "id: 3" in content
    assert "id: 1" not in content
    assert "id: 2" not in content


def test_sse_payload_sanitization():
    from app.db.session import SessionLocal
    from app.models.domain import JobEvent, ProcessingJob

    job_id = "job-sse-sanitization-200"
    db = SessionLocal()
    try:
        job = ProcessingJob(
            id=job_id,
            target_type="BIDDER",
            target_id="bidder-sse-200",
            job_type="VERIFY_BIDDER",
            status=JobStatus.FAILED,
        )
        db.add(job)
        db.commit()

        ev = JobEvent(
            job_id=job_id,
            seq=1,
            stage=JobStage.VERIFICATION,
            status=JobStatus.FAILED,
            progress=50,
            message="Traceback (most recent call last):\nFile 'app/service.py', line 99, in run\n    raise ValueError('Secret DB credentials leak')",
            payload={"details": "File 'secret.py', line 12"},
        )
        db.add(ev)
        db.commit()
    finally:
        db.close()

    headers = get_auth_headers()
    res = client.get(f"/api/v1/jobs/{job_id}/events", headers=headers)
    assert res.status_code == 200
    content = res.text
    assert "Secret DB credentials leak" not in content
    assert "An error occurred during background job processing." in content


def test_sse_authorization_and_404():
    # 404 Not Found for non-existent job
    res_404 = client.get("/api/v1/jobs/non-existent-job-uuid-9999/events", headers=get_auth_headers())
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] in ("NOT_FOUND", "HTTP_404")

    # 401 Unauthenticated
    res_401 = client.get("/api/v1/jobs/some-job-id/events")
    assert res_401.status_code == 401
    assert res_401.json()["error"]["code"] == "UNAUTHENTICATED"

