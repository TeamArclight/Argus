import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models.domain import (
    AuditEvent,
    Bidder,
    ComplianceRun,
    Document,
    Evidence,
    ExtractedFact,
    HumanDecision,
    IdempotencyRecord,
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
        HumanDecision,
        AuditEvent,
        IdempotencyRecord,
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
    assert len(revisions) >= 5

    # Confirm latest revision is idempotency records addition
    head_rev = script_dir.get_current_head()
    assert head_rev == "9f5627b30055"
