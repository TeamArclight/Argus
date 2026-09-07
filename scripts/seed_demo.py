import os
import sys
from datetime import datetime, timezone
import uuid
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add services/api to python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "api"))

from app.core.config import settings
from app.models.domain import Base, Bidder, Document, ProcessingJob, Tender, TenderRequirement
from app.schemas.canonical import DocumentType, HumanDecisionStatus, JobStatus, OperatorEnum, RequirementType


def is_disposable_database(db_url: str) -> bool:
    """Verifies that the target database is positively identified as disposable/demo."""
    url_lower = db_url.lower()
    return any(marker in url_lower for marker in ("demo", "disposable", "test", "seed", "tmp"))


def seed_demo_data() -> None:
    allow_seed = os.environ.get("ALLOW_DEMO_SEED", "").lower() in ("true", "1", "yes")
    db_url = settings.DATABASE_URL

    print(f"Target Database URL: {db_url}")

    if not allow_seed:
        print("ERROR: Demo seeding refused. ALLOW_DEMO_SEED=true is required in environment.")
        sys.exit(1)

    if not is_disposable_database(db_url):
        print("ERROR: Demo seeding refused. Target database URL must be a positively identified disposable/demo database (containing 'demo', 'disposable', 'test', or 'tmp'). Never run seed against production or development databases.")
        sys.exit(1)

    print("Confirmed disposable database and ALLOW_DEMO_SEED=true. Proceeding with synthetic demo data seed...")

    engine = create_engine(db_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        # Seed Synthetic Tender #1: GeM High Voltage Transformer Procurement
        tender_1 = db.query(Tender).filter(Tender.tender_number == "GEM/2026/B/8901234").first()
        if not tender_1:
            tender_1 = Tender(
                tender_number="GEM/2026/B/8901234",
                title="Supply and Installation of 33kV Power Transformers",
                category="Electrical Equipment",
                authority="State Power Distribution Corporation Ltd",
                budget=50000000.0,
                status=JobStatus.COMPLETED,
                metadata_json={"seed_source": "SYNTHETIC_DEMO"},
            )
            db.add(tender_1)
            db.commit()
            db.refresh(tender_1)

        # Seed Tender Requirements (Mandatory Turnover & Experience)
        req_turnover = db.query(TenderRequirement).filter(
            TenderRequirement.tender_id == tender_1.id,
            TenderRequirement.field == "turnover",
        ).first()
        if not req_turnover:
            req_turnover = TenderRequirement(
                tender_id=tender_1.id,
                field="turnover",
                operator=OperatorEnum.GTE,
                value=20000000.0,  # 2 Crore INR
                requirement_type=RequirementType.FINANCIAL,
                is_mandatory=True,
                is_approved=True,
                summary="Minimum average annual turnover of INR 2 Crore over last 3 fiscal years.",
            )
            db.add(req_turnover)

        req_exp = db.query(TenderRequirement).filter(
            TenderRequirement.tender_id == tender_1.id,
            TenderRequirement.field == "experience_years",
        ).first()
        if not req_exp:
            req_exp = TenderRequirement(
                tender_id=tender_1.id,
                field="experience_years",
                operator=OperatorEnum.GTE,
                value=5.0,
                requirement_type=RequirementType.TECHNICAL,
                is_mandatory=True,
                is_approved=True,
                summary="Minimum 5 years operational experience in power transformer manufacturing.",
            )
            db.add(req_exp)

        db.commit()

        # Seed Synthetic Bidder #1: Fully Compliant Alpha Electricals
        bidder_alpha = db.query(Bidder).filter(
            Bidder.tender_id == tender_1.id,
            Bidder.bidder_name == "Alpha Electricals Private Limited",
        ).first()
        if not bidder_alpha:
            bidder_alpha = Bidder(
                tender_id=tender_1.id,
                bidder_name="Alpha Electricals Private Limited",
                gstin="27AAAAA0000A1Z5",
                udyam_number="UDYAM-MH-01-0001234",
                cin="U31100MH2015PTC261234",
                pan="AAAAA0000A",
                status=HumanDecisionStatus.PENDING,
                metadata_json={"seed_source": "SYNTHETIC_DEMO", "scenario": "FULLY_COMPLIANT"},
            )
            db.add(bidder_alpha)

        # Seed Synthetic Bidder #2: Non-Compliant Beta Power (Turnover Failure)
        bidder_beta = db.query(Bidder).filter(
            Bidder.tender_id == tender_1.id,
            Bidder.bidder_name == "Beta Power Solutions",
        ).first()
        if not bidder_beta:
            bidder_beta = Bidder(
                tender_id=tender_1.id,
                bidder_name="Beta Power Solutions",
                gstin="07BBBBB0000B1Z2",
                udyam_number="UDYAM-DL-02-0005678",
                cin="U31100DL2020PTC365678",
                pan="BBBBB0000B",
                status=HumanDecisionStatus.PENDING,
                metadata_json={"seed_source": "SYNTHETIC_DEMO", "scenario": "TURNOVER_FAILURE"},
            )
            db.add(bidder_beta)

        db.commit()
        print("Demo data seeded successfully with synthetic compliant and failure scenarios!")

    finally:
        db.close()


if __name__ == "__main__":
    seed_demo_data()
