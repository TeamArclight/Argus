import pytest
from fastapi.testclient import TestClient
from app.db.session import Base, engine
from app.main import app
from app.schemas.canonical import UserRole
from tests.auth_helpers import get_auth_headers

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_health_check():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "argus-api"}


def test_empty_evaluations_produce_unknown():
    headers = get_auth_headers(UserRole.ADMIN)
    with TestClient(app) as client:
        # Create tender without requirements
        t_payload = {
            "tender_number": "GEM/2026/EMPTY/001",
            "title": "Tender Without Requirements",
        }
        t_resp = client.post("/api/v1/tenders", json=t_payload, headers=headers).json()
        tender_id = t_resp["id"]

        # Create bidder
        b_payload = {
            "bidder_name": "No Requirement Bidder",
            "gstin": "27AAAAA0000A1Z5",
            "metadata_json": {"verification_mode": "demo"},
        }
        b_resp = client.post(f"/api/v1/tenders/{tender_id}/bidders", json=b_payload, headers=headers).json()
        bidder_id = b_resp["id"]

        # Trigger compliance evaluation
        c_resp = client.get(f"/api/v1/bidders/{bidder_id}/compliance", headers=headers).json()
        assert c_resp["overall_status"] == "UNKNOWN"
        assert len(c_resp["rule_evaluations"]) == 0


def test_end_to_end_p0_workflow(monkeypatch):
    headers = get_auth_headers(role=UserRole.ADMIN, user_id="OFFICER-4021", name="Rajesh Kumar")
    with TestClient(app) as client:
        # 1. Create Tender
        tender_payload = {
            "tender_number": "GEM/2026/B/882190",
            "title": "Supply and Maintenance of High Performance Computing Server Clusters",
            "category": "IT Infrastructure",
            "authority": "Defence Research and Development Organisation",
            "budget": 500000000.0,
            "raw_document_uri": "s3://gem-tenders/GEM-2026-B-882190.pdf",
            "metadata_json": None,
        }
        resp = client.post("/api/v1/tenders", json=tender_payload, headers=headers)
        assert resp.status_code == 201
        tender = resp.json()
        tender_id = tender["id"]
        assert tender["tender_number"] == "GEM/2026/B/882190"

        # 2. Extract Requirements (mock intelligence service response for e2e workflow)
        from app.schemas.canonical import AIServiceResult, RequirementType, OperatorEnum
        from app.api.v1.tenders import ai_adapter as tender_ai_adapter

        async def mock_extract_tender(tender_id, document_uri="", **kwargs):
            return AIServiceResult(
                success=True,
                data=[
                    {
                        "clause": "3.1",
                        "requirement_type": RequirementType.TURNOVER,
                        "field": "financial.average_annual_turnover",
                        "operator": OperatorEnum.GTE,
                        "expected_value": 100000000,
                        "unit": "INR",
                        "mandatory": True,
                    },
                    {
                        "clause": "3.4",
                        "requirement_type": RequirementType.BLACK_LIST,
                        "field": "debarment.status",
                        "operator": OperatorEnum.EQ,
                        "expected_value": False,
                        "mandatory": True,
                    },
                    {
                        "clause": "4.1",
                        "requirement_type": RequirementType.GST,
                        "field": "general.gstin",
                        "operator": OperatorEnum.EXISTS,
                        "expected_value": True,
                        "mandatory": True,
                    },
                ],
                message="Extracted requirements",
            )

        monkeypatch.setattr(tender_ai_adapter, "extract_tender", mock_extract_tender)

        resp = client.post(f"/api/v1/tenders/{tender_id}/process", headers=headers)
        assert resp.status_code == 200
        job = resp.json()
        assert job["status"] == "COMPLETED"

        resp = client.get(f"/api/v1/tenders/{tender_id}/requirements", headers=headers)
        assert resp.status_code == 200
        requirements = resp.json()
        assert len(requirements) >= 3

        # 3. Create Bidder
        bidder_payload = {
            "bidder_name": "Bharat Cybernetics Pvt Ltd",
            "gstin": "27AAAAA0000A1Z5",
            "udyam_number": "UDYAM-MH-01-0012345",
            "cin": "U72900MH2015PTC261234",
            "pan": "ABCDE1234F",
            "metadata_json": {"verification_mode": "demo"},
        }
        resp = client.post(f"/api/v1/tenders/{tender_id}/bidders", json=bidder_payload, headers=headers)
        assert resp.status_code == 201
        bidder = resp.json()
        bidder_id = bidder["id"]
        assert bidder["status"] == "PENDING"

        # 4. Trigger Verification & Compliance Workflow
        resp = client.post(f"/api/v1/bidders/{bidder_id}/verify", headers=headers)
        assert resp.status_code == 200
        job = resp.json()
        assert job["job_type"] == "VERIFY_BIDDER"

        # 5. Fetch Compliance Overview
        resp = client.get(f"/api/v1/bidders/{bidder_id}/compliance", headers=headers)
        assert resp.status_code == 200
        compliance = resp.json()
        assert compliance["bidder_id"] == bidder_id
        assert len(compliance["rule_evaluations"]) >= 3
        assert compliance["overall_status"] in ("PASS", "FAIL", "REVIEW_REQUIRED", "UNKNOWN")

        # 6. Fetch Evaluation Evidence Trace
        rule_eval_id = compliance["rule_evaluations"][0]["id"]
        resp = client.get(f"/api/v1/evaluations/{rule_eval_id}/evidence", headers=headers)
        assert resp.status_code == 200
        evidence = resp.json()
        assert isinstance(evidence, list)

        # 7. Record Procurement Officer Decision
        decision_payload = {
            "status": "QUALIFIED",
            "reason_code": "ALL_RULES_PASSED",
            "remarks": "Bidder meets all turnover and debarment requirements with valid GST registration.",
        }
        resp = client.post(f"/api/v1/bidders/{bidder_id}/decision", json=decision_payload, headers=headers)
        assert resp.status_code == 201
        decision = resp.json()
        assert decision["status"] == "QUALIFIED"
        assert decision["officer_id"] == "OFFICER-4021"
        assert decision["officer_name"] == "Rajesh Kumar"

        # Verify bidder status updated
        resp = client.get(f"/api/v1/bidders/{bidder_id}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "QUALIFIED"

        # 8. Fetch Summary Report
        resp = client.get(f"/api/v1/bidders/{bidder_id}/report", headers=headers)
        assert resp.status_code == 200
        report = resp.json()
        assert report["bidder"]["id"] == bidder_id
        assert report["compliance_overview"]["human_decision_status"] == "QUALIFIED"
        assert report["audit_trail_count"] >= 4

