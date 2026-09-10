"""Seed a disposable database with the ARGUS synthetic demo scenario.

Audit finding H-12: this script targeted a schema that no longer exists. It
referenced RequirementType.FINANCIAL / .TECHNICAL (neither is a member of the
enum), constructed TenderRequirement with value / is_mandatory / summary (none
are columns), omitted the NOT NULL `clause`, and called Base.metadata.create_all
in contradiction of the migrations-only policy that
tests/test_startup_no_create_all.py exists to protect.

It now targets the current models and ASSUMES MIGRATIONS ARE ALREADY APPLIED:

    cd services/api && python -m alembic upgrade head
    ALLOW_DEMO_SEED=true DATABASE_URL=<disposable-url> python scripts/seed_demo.py

The tender, requirements and bidder identifiers below mirror the documents in
data/demo/, so a seeded database lines up with the demo corpus:

    ALPHA   -- compliant on every criterion
    BHARAT  -- turnover below the threshold, and a claim/document discrepancy
    CREST   -- compliant on turnover, missing Udyam registration

Existing guardrails are preserved unchanged: ALLOW_DEMO_SEED must be true AND
the target database URL must be positively identified as disposable.
"""
import os
import sys

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

# Add services/api to python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "api"))

from app.core.config import settings
from app.models.domain import Bidder, Tender, TenderRequirement
from app.schemas.canonical import (
    HumanDecisionStatus,
    JobStatus,
    OperatorEnum,
    RequirementType,
)

#: Tables the seed writes to. Their absence means migrations have not been run.
REQUIRED_TABLES = ("tenders", "tender_requirements", "bidders")

DEMO_TENDER_NUMBER = "GEM/2026/B/4521089"


def is_disposable_database(db_url: str) -> bool:
    """Verifies that the target database is positively identified as disposable/demo."""
    url_lower = db_url.lower()
    return any(marker in url_lower for marker in ("demo", "disposable", "test", "seed", "tmp"))
def assert_schema_present(engine) -> None:
    """Fails clearly when migrations have not been applied.

    The seed never creates tables. Schema management belongs to Alembic alone.
    """
    existing = set(inspect(engine).get_table_names())
    missing = [t for t in REQUIRED_TABLES if t not in existing]
    if missing:
        print(
            "ERROR: Demo seeding refused. Missing tables: "
            + ", ".join(missing)
            + ".\n       Apply migrations first:  cd services/api && python -m alembic upgrade head"
        )
        sys.exit(1)


def build_requirements(tender_id: str) -> list[TenderRequirement]:
    """Approved eligibility rules matching SECTION 2 of the demo tender document.

    Seeded as is_approved=True because these stand in for rules a procurement
    officer has already reviewed. AI-extracted candidates always arrive
    unapproved and the compliance engine refuses to evaluate them.
    """
    return [
        TenderRequirement(
            tender_id=tender_id,
            clause="2.1",
            requirement_type=RequirementType.GST,
            field="tax.gstin",
            operator=OperatorEnum.EXISTS,
            expected_value=True,
            mandatory=True,
            confidence=1.0,
            requires_verification=True,
            is_approved=True,
            source_page=1,
            source_text="The bidder must have a valid GSTIN and must be registered on the GeM portal.",
            metadata_json={"seed_source": "SYNTHETIC_DEMO"},
        ),
        TenderRequirement(
            tender_id=tender_id,
            clause="2.2",
            requirement_type=RequirementType.UDYAM,
            field="registration.udyam",
            operator=OperatorEnum.EXISTS,
            expected_value=True,
            mandatory=True,
            confidence=1.0,
            requires_verification=True,
            is_approved=True,
            source_page=1,
            source_text="MSE bidders shall furnish a valid Udyam Registration Certificate.",
            metadata_json={"seed_source": "SYNTHETIC_DEMO"},
        ),
        TenderRequirement(
            tender_id=tender_id,
            clause="2.3",
            requirement_type=RequirementType.TURNOVER,
            field="financial.average_annual_turnover",
            operator=OperatorEnum.GTE,
            expected_value=10000000,  # INR 1,00,00,000 (One Crore)
            unit="INR",
            mandatory=True,
            confidence=1.0,
            requires_verification=True,
            is_approved=True,
            source_page=1,
            source_text=(
                "Minimum annual turnover shall be INR 1,00,00,000 based on the average of "
                "the last three financial years (FY 2023-24, 2024-25, 2025-26)."
            ),
            metadata_json={"seed_source": "SYNTHETIC_DEMO", "currency": "INR"},
        ),
        TenderRequirement(
            tender_id=tender_id,
            clause="2.4",
            requirement_type=RequirementType.EXPERIENCE,
            field="experience.years",
            operator=OperatorEnum.GTE,
            expected_value=3,
            unit="years",
            mandatory=True,
            confidence=1.0,
            requires_verification=False,
            is_approved=True,
            source_page=1,
            source_text=(
                "The bidder must have a minimum of 3 years of experience in supply and "
                "installation of IT infrastructure equipment to government or PSU organizations."
            ),
            metadata_json={"seed_source": "SYNTHETIC_DEMO"},
        ),
        TenderRequirement(
            tender_id=tender_id,
            clause="2.5",
            requirement_type=RequirementType.BLACK_LIST,
            field="legal.blacklisted",
            operator=OperatorEnum.EQ,
            expected_value=False,
            mandatory=True,
            confidence=1.0,
            requires_verification=True,
            is_approved=True,
            source_page=1,
            source_text=(
                "The bidder must not be blacklisted or debarred by any Central/State Government "
                "organization."
            ),
            metadata_json={"seed_source": "SYNTHETIC_DEMO"},
        ),
    ]


def build_bidders(tender_id: str) -> list[Bidder]:
    """Bidder records matching the identifiers in data/demo/bidder_*/."""
    return [
        Bidder(
            tender_id=tender_id,
            bidder_name="ALPHA TECHNOLOGIES PRIVATE LIMITED",
            gstin="07AABCA1234H1Z9",
            udyam_number="UDYAM-DL-07-0012345",
            pan="AABCA1234H",
            status=HumanDecisionStatus.PENDING,
            metadata_json={"seed_source": "SYNTHETIC_DEMO", "scenario": "FULLY_COMPLIANT"},
        ),
        Bidder(
            tender_id=tender_id,
            bidder_name="BHARAT INFOSYSTEMS LLP",
            gstin="29AAFBB5678K1Z3",
            udyam_number="UDYAM-KA-29-0045678",
            pan="AAFBB5678K",
            status=HumanDecisionStatus.PENDING,
            metadata_json={
                "seed_source": "SYNTHETIC_DEMO",
                "scenario": "TURNOVER_SHORTFALL_AND_CLAIM_DISCREPANCY",
            },
        ),
        Bidder(
            tender_id=tender_id,
            bidder_name="CREST SOLUTIONS PRIVATE LIMITED",
            gstin="27AABCC9876D1Z7",
            udyam_number=None,
            pan="AABCC9876D",
            status=HumanDecisionStatus.PENDING,
            metadata_json={"seed_source": "SYNTHETIC_DEMO", "scenario": "MISSING_UDYAM_REGISTRATION"},
        ),
    ]




def seed_demo_data() -> None:
    allow_seed = os.environ.get("ALLOW_DEMO_SEED", "").lower() in ("true", "1", "yes")
    db_url = settings.DATABASE_URL

    print(f"Target Database URL: {db_url}")

    if not allow_seed:
        print("ERROR: Demo seeding refused. ALLOW_DEMO_SEED=true is required in environment.")
        sys.exit(1)

    if not is_disposable_database(db_url):
        print(
            "ERROR: Demo seeding refused. Target database URL must be a positively identified "
            "disposable/demo database (containing 'demo', 'disposable', 'test', or 'tmp'). "
            "Never run seed against production or development databases."
        )
        sys.exit(1)

    print("Confirmed disposable database and ALLOW_DEMO_SEED=true. Proceeding with synthetic demo data seed...")

    engine = create_engine(db_url)
    assert_schema_present(engine)

    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        tender = db.query(Tender).filter(Tender.tender_number == DEMO_TENDER_NUMBER).first()
        if not tender:
            tender = Tender(
                tender_number=DEMO_TENDER_NUMBER,
                title=(
                    "Supply of IT Infrastructure Equipment — Server Racks, UPS Systems, "
                    "and Networking Hardware"
                ),
                category="IT Hardware / Data Centre Infrastructure",
                authority=(
                    "National Informatics Centre (NIC), Ministry of Electronics and "
                    "Information Technology"
                ),
                budget=50000000.0,
                status=JobStatus.COMPLETED,
                metadata_json={"seed_source": "SYNTHETIC_DEMO"},
            )
            db.add(tender)
            db.commit()
            db.refresh(tender)
            print(f"  Created tender {tender.tender_number} ({tender.id})")
        else:
            print(f"  Reusing existing tender {tender.tender_number} ({tender.id})")

        existing_clauses = {
            r.clause
            for r in db.query(TenderRequirement).filter(TenderRequirement.tender_id == tender.id).all()
        }
        added_requirements = 0
        for requirement in build_requirements(tender.id):
            if requirement.clause in existing_clauses:
                continue
            db.add(requirement)
            added_requirements += 1

        existing_bidder_names = {
            b.bidder_name for b in db.query(Bidder).filter(Bidder.tender_id == tender.id).all()
        }
        added_bidders = 0
        for bidder in build_bidders(tender.id):
            if bidder.bidder_name in existing_bidder_names:
                continue
            db.add(bidder)
            added_bidders += 1

        db.commit()
        print(f"  Added {added_requirements} approved requirement(s)")
        print(f"  Added {added_bidders} bidder(s)")
        print(
            "Demo data seeded successfully. Upload the matching PDFs from data/demo/pdf/ "
            "to exercise the full ingestion pipeline."
        )
    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


if __name__ == "__main__":
    seed_demo_data()
