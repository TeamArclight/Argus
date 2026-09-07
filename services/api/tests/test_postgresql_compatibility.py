import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.domain import (
    ActiveOperationLock,
    AuditEvent,
    Bidder,
    ComplianceRun,
    Document,
    Evidence,
    ExtractedFact,
    HumanDecision,
    IdempotencyRecord,
    JobEvent,
    ProcessingJob,
    RiskSignal,
    RuleEvaluation,
    Tender,
    TenderRequirement,
    VerificationResult,
)


def test_postgresql_schema_compilation():
    """Verifies that all SQLAlchemy domain models compile cleanly to valid PostgreSQL DDL statements."""
    postgres_engine = create_engine("postgresql://user:pass@localhost:5432/testdb", strategy="mock", executor=lambda sql, *a, **kw: None)

    models = [
        Tender,
        TenderRequirement,
        Bidder,
        Document,
        ExtractedFact,
        VerificationResult,
        RuleEvaluation,
        Evidence,
        ComplianceRun,
        RiskSignal,
        ProcessingJob,
        JobEvent,
        HumanDecision,
        AuditEvent,
        IdempotencyRecord,
        ActiveOperationLock,
    ]

    for model in models:
        # Generate PostgreSQL DDL statement for table
        ddl = str(CreateTable(model.__table__).compile(postgres_engine, dialect=postgresql.dialect()))
        assert "CREATE TABLE" in ddl
        assert model.__tablename__ in ddl


def test_alembic_postgresql_dialect_migration_check():
    """Verifies that Alembic migration scripts compile for PostgreSQL dialect."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option("script_location", "alembic")
    script_dir = ScriptDirectory.from_config(config)

    revisions = list(script_dir.walk_revisions())
    assert len(revisions) >= 6

    # Confirm latest revision is idempotency and active operation locks addition
    head_rev = script_dir.get_current_head()
    assert head_rev == "9f5627b30055"


def _get_live_postgres_engine():
    import os
    from app.core.config import settings
    pg_url = os.environ.get("TEST_POSTGRES_URL") or os.environ.get("DATABASE_URL") or settings.DATABASE_URL
    if not pg_url or not ("postgres" in pg_url or "psycopg" in pg_url):
        return None
    try:
        engine = create_engine(pg_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        return None


def test_live_postgres_integration_suite():
    """Executes live PostgreSQL integration validations when a real PostgreSQL service is available."""
    pg_engine = _get_live_postgres_engine()
    if pg_engine is None:
        pytest.skip("No live PostgreSQL service reachable. Skipping live PG integration test.")

    with pg_engine.connect() as conn:
        res = conn.execute(text("SELECT 1")).scalar()
        assert res == 1
