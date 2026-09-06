import time
import pytest
import jwt
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import Base, SessionLocal, engine
from app.main import app
from app.models.domain import AuditEvent, Bidder, ComplianceRun, HumanDecision, HumanDecisionStatus, Tender
from app.schemas.canonical import UserRole
from tests.auth_helpers import get_auth_headers


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
def sample_tender_and_bidder(db: Session):
    tender = Tender(
        tender_number="TNT-AUTH-001",
        title="Auth Test Tender",
    )
    db.add(tender)
    db.commit()
    db.refresh(tender)

    bidder = Bidder(
        tender_id=tender.id,
        bidder_name="Auth Test Corp",
        status=HumanDecisionStatus.PENDING,
    )
    db.add(bidder)
    db.commit()
    db.refresh(bidder)

    return {"tender": tender, "bidder": bidder}


def test_public_endpoint_accessible_without_token():
    with TestClient(app) as client:
        res = client.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "ok"


def test_protected_endpoints_require_token():
    with TestClient(app) as client:
        # GET /api/v1/auth/me
        res_me = client.get("/api/v1/auth/me")
        assert res_me.status_code == 401

        # GET /api/v1/tenders
        res_tenders = client.get("/api/v1/tenders")
        assert res_tenders.status_code == 401

        # POST /api/v1/tenders
        res_create_tender = client.post("/api/v1/tenders", json={"tender_number": "T1", "title": "T1"})
        assert res_create_tender.status_code == 401

        # GET /api/v1/audit/events
        res_audit = client.get("/api/v1/audit/events")
        assert res_audit.status_code == 401


def test_invalid_jwt_tokens_rejected():
    with TestClient(app) as client:
        # 1. Malformed token
        res = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-valid-jwt-token"})
        assert res.status_code == 401
        msg = res.json().get("detail") or res.json().get("error", {}).get("message", "")
        assert "Invalid authorization token" in msg

        # 2. Wrong signature secret
        bad_sig_token = jwt.encode(
            {
                "sub": "user-1",
                "role": "ADMIN",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
                "iss": settings.ARGUS_JWT_ISSUER,
                "aud": settings.ARGUS_JWT_AUDIENCE,
            },
            "wrong-secret-key-12345678901234567890",
            algorithm="HS256",
        )
        res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_sig_token}"})
        assert res.status_code == 401

        # 3. Expired token
        expired_token = jwt.encode(
            {
                "sub": "user-1",
                "role": "ADMIN",
                "exp": datetime.now(timezone.utc) - timedelta(minutes=10),
                "iss": settings.ARGUS_JWT_ISSUER,
                "aud": settings.ARGUS_JWT_AUDIENCE,
            },
            settings.ARGUS_JWT_SECRET,
            algorithm="HS256",
        )
        res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"})
        assert res.status_code == 401
        msg = res.json().get("detail") or res.json().get("error", {}).get("message", "")
        assert "Token has expired" in msg or "Signature has expired" in msg or "expired" in msg.lower()

        # 4. Wrong Issuer
        wrong_iss_token = jwt.encode(
            {
                "sub": "user-1",
                "role": "ADMIN",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
                "iss": "invalid-issuer",
                "aud": settings.ARGUS_JWT_AUDIENCE,
            },
            settings.ARGUS_JWT_SECRET,
            algorithm="HS256",
        )
        res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {wrong_iss_token}"})
        assert res.status_code == 401
        msg = res.json().get("detail") or res.json().get("error", {}).get("message", "")
        assert "Invalid" in msg or "issuer" in msg.lower()

        # 5. Wrong Audience
        wrong_aud_token = jwt.encode(
            {
                "sub": "user-1",
                "role": "ADMIN",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
                "iss": settings.ARGUS_JWT_ISSUER,
                "aud": "invalid-audience",
            },
            settings.ARGUS_JWT_SECRET,
            algorithm="HS256",
        )
        res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {wrong_aud_token}"})
        assert res.status_code == 401

        # 6. Missing subject claim (sub)
        no_sub_token = jwt.encode(
            {
                "role": "ADMIN",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
                "iss": settings.ARGUS_JWT_ISSUER,
                "aud": settings.ARGUS_JWT_AUDIENCE,
            },
            settings.ARGUS_JWT_SECRET,
            algorithm="HS256",
        )
        res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {no_sub_token}"})
        assert res.status_code == 401

        # 7. Invalid role claim
        bad_role_token = jwt.encode(
            {
                "sub": "user-1",
                "role": "SUPERUSER",
                "exp": datetime.now(timezone.utc) + timedelta(minutes=10),
                "iss": settings.ARGUS_JWT_ISSUER,
                "aud": settings.ARGUS_JWT_AUDIENCE,
            },
            settings.ARGUS_JWT_SECRET,
            algorithm="HS256",
        )
        res = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_role_token}"})
        assert res.status_code == 401



def test_auth_me_endpoint_returns_principal_profile():
    headers = get_auth_headers(role=UserRole.PROCUREMENT_OFFICER, user_id="PO-77", name="Priya Sharma")
    with TestClient(app) as client:
        res = client.get("/api/v1/auth/me", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["user_id"] == "PO-77"
        assert data["role"] == "PROCUREMENT_OFFICER"
        assert data["name"] == "Priya Sharma"


def test_rbac_decision_endpoint_permissions(sample_tender_and_bidder):
    bidder = sample_tender_and_bidder["bidder"]
    decision_payload = {
        "status": "QUALIFIED",
        "reason_code": "VERIFIED_COMPLIANT",
        "remarks": "Fully compliant with procurement rules",
    }

    with TestClient(app) as client:
        # REVIEWER role gets 403 Forbidden
        headers_reviewer = get_auth_headers(role=UserRole.REVIEWER, user_id="REV-01")
        res_rev = client.post(f"/api/v1/bidders/{bidder.id}/decision", json=decision_payload, headers=headers_reviewer)
        assert res_rev.status_code == 403

        # AUDITOR role gets 403 Forbidden
        headers_auditor = get_auth_headers(role=UserRole.AUDITOR, user_id="AUD-01")
        res_aud = client.post(f"/api/v1/bidders/{bidder.id}/decision", json=decision_payload, headers=headers_auditor)
        assert res_aud.status_code == 403

        # PROCUREMENT_OFFICER gets 201 Created
        headers_po = get_auth_headers(role=UserRole.PROCUREMENT_OFFICER, user_id="PO-01", name="Officer Jane")
        res_po = client.post(f"/api/v1/bidders/{bidder.id}/decision", json=decision_payload, headers=headers_po)
        assert res_po.status_code == 201
        data = res_po.json()
        assert data["officer_id"] == "PO-01"
        assert data["officer_name"] == "Officer Jane"


def test_identity_spoofing_prevention_on_decision_payload(sample_tender_and_bidder, db: Session):
    bidder = sample_tender_and_bidder["bidder"]
    headers_po = get_auth_headers(role=UserRole.PROCUREMENT_OFFICER, user_id="REAL-OFFICER-99", name="Real Officer")

    # Client payload attempting to inject officer_id / officer_name
    spoof_payload = {
        "status": "QUALIFIED",
        "reason_code": "VERIFIED",
        "remarks": "Trying to spoof officer identity",
        "officer_id": "FAKE-OFFICER-007",
        "officer_name": "Fake Name",
    }

    with TestClient(app) as client:
        # Request should fail with 422 due to extra="forbid"
        res = client.post(f"/api/v1/bidders/{bidder.id}/decision", json=spoof_payload, headers=headers_po)
        assert res.status_code == 422

    # Clean payload without extra fields must succeed and record REAL officer identity
    clean_payload = {
        "status": "QUALIFIED",
        "reason_code": "VERIFIED",
        "remarks": "Authentic officer decision",
    }
    with TestClient(app) as client:
        res = client.post(f"/api/v1/bidders/{bidder.id}/decision", json=clean_payload, headers=headers_po)
        assert res.status_code == 201
        data = res.json()
        assert data["officer_id"] == "REAL-OFFICER-99"
        assert data["officer_name"] == "Real Officer"

    # Verify database record strictly used principal identity
    decision = db.query(HumanDecision).filter(HumanDecision.bidder_id == bidder.id).first()
    assert decision.officer_id == "REAL-OFFICER-99"
    assert decision.officer_name == "Real Officer"

    # Verify audit event actor attribution
    audit = db.query(AuditEvent).filter(AuditEvent.entity_id == bidder.id, AuditEvent.action == "HUMAN_DECISION_RECORDED").first()
    assert audit is not None
    assert audit.actor_id == "REAL-OFFICER-99"
    assert audit.actor_role == "PROCUREMENT_OFFICER"


def test_verification_workflow_records_authenticated_actor(sample_tender_and_bidder, db: Session):
    bidder = sample_tender_and_bidder["bidder"]
    headers = get_auth_headers(role=UserRole.PROCUREMENT_OFFICER, user_id="ACTOR-123", name="Trigger Officer")

    with TestClient(app) as client:
        res = client.post(f"/api/v1/bidders/{bidder.id}/verify", headers=headers)
        assert res.status_code == 200

    # Verify ComplianceRun.triggered_by
    run = db.query(ComplianceRun).filter(ComplianceRun.bidder_id == bidder.id).first()
    assert run is not None
    assert run.triggered_by == "ACTOR-123"

    # Verify AuditEvent actor attribution
    audit = db.query(AuditEvent).filter(AuditEvent.entity_id == bidder.id, AuditEvent.action == "VERIFICATION_STARTED").first()
    assert audit is not None
    assert audit.actor_id == "ACTOR-123"
    assert audit.actor_role == "PROCUREMENT_OFFICER"


def test_auditor_and_reviewer_read_access(sample_tender_and_bidder):
    bidder = sample_tender_and_bidder["bidder"]

    for role in [UserRole.REVIEWER, UserRole.AUDITOR]:
        headers = get_auth_headers(role=role, user_id=f"user-{role.value}")
        with TestClient(app) as client:
            # GET /bidders/{id}/compliance
            res_comp = client.get(f"/api/v1/bidders/{bidder.id}/compliance", headers=headers)
            assert res_comp.status_code == 200

            # GET /bidders/{id}/runs
            res_runs = client.get(f"/api/v1/bidders/{bidder.id}/runs", headers=headers)
            assert res_runs.status_code == 200

            # GET /bidders/{id}/report
            res_rep = client.get(f"/api/v1/bidders/{bidder.id}/report", headers=headers)
            assert res_rep.status_code == 200

    # AUDITOR can access /audit/events
    headers_auditor = get_auth_headers(role=UserRole.AUDITOR, user_id="aud-1")
    with TestClient(app) as client:
        res_audit = client.get("/api/v1/audit/events", headers=headers_auditor)
        assert res_audit.status_code == 200

    # REVIEWER cannot access /audit/events (403 Forbidden)
    headers_reviewer = get_auth_headers(role=UserRole.REVIEWER, user_id="rev-1")
    with TestClient(app) as client:
        res_audit = client.get("/api/v1/audit/events", headers=headers_reviewer)
        assert res_audit.status_code == 403

