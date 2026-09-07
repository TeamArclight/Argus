import os
from pathlib import Path
import pytest
from sqlalchemy import create_engine, inspect
from alembic.config import Config
from alembic import command


from alembic.migration import MigrationContext
from sqlalchemy import text


def test_alembic_migration_upgrade_and_tables():
    """Smoke test ensuring Alembic can upgrade a fresh database to head, create all required tables, and track active revision."""
    api_dir = Path(__file__).resolve().parent.parent
    ini_path = api_dir / "alembic.ini"
    test_db_path = api_dir / "test_migration_smoke.db"

    if test_db_path.exists():
        test_db_path.unlink()

    db_url = f"sqlite:///{test_db_path}"

    alembic_cfg = Config(str(ini_path))
    alembic_cfg.set_main_option("script_location", str(api_dir / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

    test_engine = None
    try:
        # Run upgrade head
        command.upgrade(alembic_cfg, "head")

        # Inspect generated database
        test_engine = create_engine(db_url)
        inspector = inspect(test_engine)
        tables = set(inspector.get_table_names())

        expected_tables = {
            "tenders",
            "tender_requirements",
            "bidders",
            "documents",
            "extracted_facts",
            "compliance_runs",
            "verification_results",
            "rule_evaluations",
            "risk_signals",
            "evidence",
            "processing_jobs",
            "job_events",
            "human_decisions",
            "audit_events",
            "alembic_version",
        }

        assert expected_tables.issubset(tables), f"Missing tables: {expected_tables - tables}"

        # Verify Alembic migration context reports expected baseline revision ID
        with test_engine.connect() as connection:
            context = MigrationContext.configure(connection)
            current_rev = context.get_current_revision()
            assert current_rev == "7f3416a29033", f"Unexpected current revision: {current_rev}"

    finally:
        if test_engine is not None:
            test_engine.dispose()
        if test_db_path.exists():
            test_db_path.unlink()


def test_alembic_migration_upgrade_from_phase6_with_historical_documents():
    """Verifies upgrading an existing Phase 6 database populated with bidder-owned documents to Phase 7 head preserves historical data."""
    api_dir = Path(__file__).resolve().parent.parent
    ini_path = api_dir / "alembic.ini"
    test_db_path = api_dir / "test_migration_phase6.db"

    if test_db_path.exists():
        test_db_path.unlink()

    db_url = f"sqlite:///{test_db_path}"
    alembic_cfg = Config(str(ini_path))
    alembic_cfg.set_main_option("script_location", str(api_dir / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

    test_engine = None
    try:
        # Step 1: Upgrade to baseline 44d5c5ca3f11
        command.upgrade(alembic_cfg, "44d5c5ca3f11")

        test_engine = create_engine(db_url)
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenders (id, tender_number, title, status, metadata_json, created_at, updated_at) "
                    "VALUES ('t-hist-1', 'GEM/2026/HIST/001', 'Historical Tender', 'COMPLETED', '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO bidders (id, tender_id, bidder_name, status, metadata_json, created_at) "
                    "VALUES ('b-hist-1', 't-hist-1', 'Historical Bidder', 'PENDING', '{}', '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO documents (id, bidder_id, document_type, storage_uri, filename, sha256, metadata_json, created_at) "
                    "VALUES ('d-hist-1', 'b-hist-1', 'GST_CERT', 'bidders/b-hist-1/gst.png', 'gst.png', 'hash123', '{}', '2026-01-01 00:00:00')"
                )
            )

        # Step 2: Upgrade to head (7f3416a29033)
        command.upgrade(alembic_cfg, "head")

        # Step 3: Verify historical document row preserved
        with test_engine.connect() as conn:
            result = conn.execute(text("SELECT id, bidder_id, tender_id, document_type, filename FROM documents WHERE id = 'd-hist-1'")).fetchone()
            assert result is not None
            assert result._mapping["bidder_id"] == "b-hist-1"
            assert result._mapping["tender_id"] is None
            assert result._mapping["filename"] == "gst.png"

    finally:
        if test_engine is not None:
            test_engine.dispose()
        if test_db_path.exists():
            test_db_path.unlink()


def test_alembic_migration_upgrade_from_phase7_with_populated_intelligence_data():
    """Verifies upgrading an existing Phase 7 database populated with requirements and facts to Phase 8 head preserves data and defaults is_approved=False."""
    api_dir = Path(__file__).resolve().parent.parent
    ini_path = api_dir / "alembic.ini"
    test_db_path = api_dir / "test_migration_phase7.db"

    if test_db_path.exists():
        test_db_path.unlink()

    db_url = f"sqlite:///{test_db_path}"
    alembic_cfg = Config(str(ini_path))
    alembic_cfg.set_main_option("script_location", str(api_dir / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

    test_engine = None
    try:
        # Step 1: Upgrade to Phase 7 baseline 5b14a9f27911
        command.upgrade(alembic_cfg, "5b14a9f27911")

        test_engine = create_engine(db_url)
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenders (id, tender_number, title, status, metadata_json, created_at, updated_at) "
                    "VALUES ('t-p7-1', 'GEM/2026/P7/001', 'Phase 7 Tender', 'COMPLETED', '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO bidders (id, tender_id, bidder_name, status, metadata_json, created_at) "
                    "VALUES ('b-p7-1', 't-p7-1', 'Phase 7 Bidder', 'PENDING', '{}', '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO documents (id, bidder_id, tender_id, document_type, storage_uri, filename, sha256, metadata_json, created_at) "
                    "VALUES ('d-p7-1', NULL, 't-p7-1', 'TENDER', 'tenders/t-p7-1/tender.pdf', 'tender.pdf', 'hash123', '{}', '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO tender_requirements (id, tender_id, clause, requirement_type, field, operator, expected_value, mandatory, requires_verification, confidence, created_at) "
                    "VALUES ('req-p7-1', 't-p7-1', '1.1', 'GST', 'general.gstin', 'EXISTS', 'true', 1, 1, 0.95, '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO extracted_facts (id, bidder_id, document_id, field, value, source_page, source_text, confidence, created_at) "
                    "VALUES ('fact-p7-1', 'b-p7-1', 'd-p7-1', 'gstin', '27AAACA12341ZV', 1, 'GSTIN: 27AAACA12341ZV', 0.99, '2026-01-01 00:00:00')"
                )
            )

        # Step 2: Upgrade to head (7f3416a29033)
        command.upgrade(alembic_cfg, "head")

        # Step 3: Verify requirement and fact fields after migration
        with test_engine.connect() as conn:
            req = conn.execute(text("SELECT id, clause, is_approved, document_id, metadata_json FROM tender_requirements WHERE id = 'req-p7-1'")).fetchone()
            assert req is not None
            assert req._mapping["clause"] == "1.1"
            assert req._mapping["is_approved"] == 0 or req._mapping["is_approved"] is False
            assert req._mapping["document_id"] is None

            fact = conn.execute(text("SELECT id, field, value, metadata_json FROM extracted_facts WHERE id = 'fact-p7-1'")).fetchone()
            assert fact is not None
            assert fact._mapping["field"] == "gstin"
            assert fact._mapping["value"] == "27AAACA12341ZV"

    finally:
        if test_engine is not None:
            test_engine.dispose()
        if test_db_path.exists():
            test_db_path.unlink()


def test_alembic_migration_upgrade_from_phase8_with_populated_evidence_data():
    """Verifies upgrading an existing Phase 8 database populated with evidence and compliance runs to Phase 9 head preserves data and adds new columns."""
    api_dir = Path(__file__).resolve().parent.parent
    ini_path = api_dir / "alembic.ini"
    test_db_path = api_dir / "test_migration_phase8.db"

    if test_db_path.exists():
        test_db_path.unlink()

    db_url = f"sqlite:///{test_db_path}"
    alembic_cfg = Config(str(ini_path))
    alembic_cfg.set_main_option("script_location", str(api_dir / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

    test_engine = None
    try:
        # Step 1: Upgrade to Phase 8 baseline 6c25b8a18022
        command.upgrade(alembic_cfg, "6c25b8a18022")

        test_engine = create_engine(db_url)
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO tenders (id, tender_number, title, status, metadata_json, created_at, updated_at) "
                    "VALUES ('t-p8-1', 'GEM/2026/P8/001', 'Phase 8 Tender', 'COMPLETED', '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO bidders (id, tender_id, bidder_name, status, metadata_json, created_at) "
                    "VALUES ('b-p8-1', 't-p8-1', 'Phase 8 Bidder', 'PENDING', '{}', '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO compliance_runs (id, bidder_id, tender_id, execution_status, overall_status, summary_json, started_at, created_at) "
                    "VALUES ('run-p8-1', 'b-p8-1', 't-p8-1', 'COMPLETED', 'PASS', '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO evidence (id, entity_type, entity_id, snippet, location_metadata, created_at) "
                    "VALUES ('ev-p8-1', 'BIDDER', 'b-p8-1', 'Turnover certificate snippet', '{}', '2026-01-01 00:00:00')"
                )
            )

        # Step 2: Upgrade to head (7f3416a29033)
        command.upgrade(alembic_cfg, "head")

        # Step 3: Verify new columns on compliance_runs and evidence
        with test_engine.connect() as conn:
            run = conn.execute(text("SELECT id, input_snapshot_json FROM compliance_runs WHERE id = 'run-p8-1'")).fetchone()
            assert run is not None
            assert run._mapping["input_snapshot_json"] == "{}" or run._mapping["input_snapshot_json"] == {}

            ev = conn.execute(text("SELECT id, bidder_id, tender_id, document_id, verification_mode FROM evidence WHERE id = 'ev-p8-1'")).fetchone()
            assert ev is not None
            assert ev._mapping["bidder_id"] is None
            assert ev._mapping["verification_mode"] is None

    finally:
        if test_engine is not None:
            test_engine.dispose()
        if test_db_path.exists():
            test_db_path.unlink()




