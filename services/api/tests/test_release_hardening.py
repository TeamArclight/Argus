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
