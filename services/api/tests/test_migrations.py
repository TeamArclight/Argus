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
            assert current_rev == "5b14a9f27911", f"Unexpected current revision: {current_rev}"

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

        # Step 2: Upgrade to head (5b14a9f27911)
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


