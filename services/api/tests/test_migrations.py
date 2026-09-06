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
            assert current_rev == "44d5c5ca3f11", f"Unexpected current revision: {current_rev}"

    finally:
        if test_engine is not None:
            test_engine.dispose()
        if test_db_path.exists():
            test_db_path.unlink()

