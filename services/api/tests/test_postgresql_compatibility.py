import concurrent.futures
import os
import uuid
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
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
from app.schemas.canonical import AuthenticatedPrincipal, JobStage, JobStatus, UserRole
from app.services.idempotency_service import IdempotencyService
from app.services.job_event_service import JobEventService
from app.services.operation_lock_service import OperationLockService


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
    from app.core.config import settings
    pg_url = os.environ.get("TEST_POSTGRES_URL") or os.environ.get("DATABASE_URL") or settings.DATABASE_URL
    if not pg_url or not ("postgres" in pg_url or "psycopg" in pg_url):
        return None
    try:
        engine = create_engine(pg_url)
        db_name = engine.url.database or ""
        # Safety guard: ensure database is a disposable test database
        if "test" not in db_name.lower() and os.environ.get("ALLOW_POSTGRES_TEST") != "1":
            raise RuntimeError(f"Refusing to run live PostgreSQL tests against non-test database: {db_name}")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except Exception:
        return None


@pytest.fixture(scope="module")
def pg_session_factory():
    engine = _get_live_postgres_engine()
    if engine is None:
        pytest.skip("No live PostgreSQL test service reachable. Skipping live PG concurrency suite.")
    from app.db.session import Base
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_pg_same_key_concurrent_requests(pg_session_factory):
    """Verifies that concurrent requests with the same idempotency key are race-safely handled in PostgreSQL."""
    key = f"pg-same-key-{uuid.uuid4()}"
    principal_id = "officer-pg-1"
    resource_type = "BIDDER"
    resource_id = f"b-{uuid.uuid4()}"
    operation = "VERIFY_BIDDER"
    req_dict = {"bidder_id": resource_id, "action": "verify"}

    def attempt_start():
        with pg_session_factory() as session:
            try:
                cached_res, cached_code, rec = IdempotencyService.check_or_start(
                    session,
                    key=key,
                    principal_id=principal_id,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    operation=operation,
                    payload=req_dict,
                )
                return ("STARTED", rec.id if rec else None)
            except HTTPException as e:
                return ("BLOCKED", e.status_code, e.detail)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_start)
        f2 = executor.submit(attempt_start)
        res1 = f1.result()
        res2 = f2.result()

    statuses = [res1[0], res2[0]]
    assert statuses.count("STARTED") == 1
    assert statuses.count("BLOCKED") == 1

    blocked = res1 if res1[0] == "BLOCKED" else res2
    assert blocked[1] == 409
    assert blocked[2]["code"] == "OPERATION_IN_PROGRESS"

    # Winner completes the record
    with pg_session_factory() as session:
        rec = IdempotencyService.get_record(session, principal_id, resource_type, resource_id, operation, key)
        assert rec is not None
        IdempotencyService.complete(
            session,
            record=rec,
            response_code=200,
            response_json={"status": "VERIFIED"},
        )

    # Subsequent request gets cached response
    with pg_session_factory() as session:
        cached_res, cached_code, rec = IdempotencyService.check_or_start(
            session,
            key=key,
            principal_id=principal_id,
            resource_type=resource_type,
            resource_id=resource_id,
            operation=operation,
            payload=req_dict,
        )
        assert cached_code == 200
        assert cached_res == {"status": "VERIFIED"}


def test_pg_different_key_concurrent_requests_same_bidder(pg_session_factory):
    """Verifies that concurrent requests with different idempotency keys for the same bidder are blocked by ActiveOperationLock."""
    bidder_id = f"b-lock-{uuid.uuid4()}"
    job_id_1 = f"j-lock-1-{uuid.uuid4()}"
    job_id_2 = f"j-lock-2-{uuid.uuid4()}"

    def attempt_acquire(job_id, p_id):
        with pg_session_factory() as session:
            try:
                lock = OperationLockService.acquire_lock(
                    session,
                    resource_type="BIDDER",
                    resource_id=bidder_id,
                    operation="VERIFY_BIDDER",
                    job_id=job_id,
                    principal_id=p_id,
                )
                return ("SUCCESS", lock.id)
            except HTTPException as e:
                return ("BLOCKED", e.status_code, e.detail)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(attempt_acquire, job_id_1, "user-1")
        f2 = executor.submit(attempt_acquire, job_id_2, "user-2")
        r1 = f1.result()
        r2 = f2.result()

    statuses = [r1[0], r2[0]]
    assert statuses.count("SUCCESS") == 1
    assert statuses.count("BLOCKED") == 1

    blocked_res = r1 if r1[0] == "BLOCKED" else r2
    assert blocked_res[1] == 409
    assert blocked_res[2]["code"] == "OPERATION_IN_PROGRESS"

    # Cleanup
    with pg_session_factory() as session:
        OperationLockService.release_lock(session, "BIDDER", bidder_id, "VERIFY_BIDDER")


def test_pg_one_active_operation_enforced_by_database(pg_session_factory):
    """Verifies that PostgreSQL database unique constraint uq_active_operation_locks_resource rejects duplicate locks."""
    res_id = f"res-{uuid.uuid4()}"

    with pg_session_factory() as s1:
        l1 = ActiveOperationLock(
            resource_type="BIDDER",
            resource_id=res_id,
            operation="VERIFY_BIDDER",
            job_id=f"job-1-{uuid.uuid4()}",
            owner_principal_id="p1",
        )
        s1.add(l1)
        s1.commit()

        with pg_session_factory() as s2:
            l2 = ActiveOperationLock(
                resource_type="BIDDER",
                resource_id=res_id,
                operation="VERIFY_BIDDER",
                job_id=f"job-2-{uuid.uuid4()}",
                owner_principal_id="p2",
            )
            s2.add(l2)
            with pytest.raises(IntegrityError):
                s2.commit()
            s2.rollback()

        # Cleanup
        s1.delete(l1)
        s1.commit()


def test_pg_concurrent_job_event_sequence_allocation(pg_session_factory):
    """Verifies that concurrent JobEvent emissions on PostgreSQL allocate strictly monotonic unique sequences."""
    job_id = f"job-event-{uuid.uuid4()}"

    # Create parent ProcessingJob
    with pg_session_factory() as session:
        pj = ProcessingJob(
            id=job_id,
            target_type="BIDDER",
            target_id="b-evt",
            job_type="VERIFY_BIDDER",
            status=JobStatus.RUNNING,
            current_stage=JobStage.VERIFICATION,
            progress=10,
        )
        session.add(pj)
        session.commit()

    def emit_event(i):
        with pg_session_factory() as session:
            ev = JobEventService.emit_event(
                session,
                job_id=job_id,
                stage=JobStage.VERIFICATION,
                status=JobStatus.RUNNING,
                progress=10 + i * 5,
                message=f"Processing event {i}",
            )
            session.commit()
            return ev.seq

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(emit_event, i) for i in range(5)]
        allocated_seqs = [f.result() for f in futures]

    # Verify all 5 events have distinct sequence numbers 1..5
    assert sorted(allocated_seqs) == [1, 2, 3, 4, 5]

    with pg_session_factory() as session:
        events = session.query(JobEvent).filter_by(job_id=job_id).order_by(JobEvent.seq.asc()).all()
        assert len(events) == 5
        assert [e.seq for e in events] == [1, 2, 3, 4, 5]


def test_pg_rollback_does_not_publish_event(pg_session_factory):
    """Verifies that a rolled-back transaction in PostgreSQL leaves no committed JobEvent rows."""
    job_id = f"job-rb-{uuid.uuid4()}"

    with pg_session_factory() as session:
        pj = ProcessingJob(
            id=job_id,
            target_type="BIDDER",
            target_id="b-rb",
            job_type="VERIFY_BIDDER",
            status=JobStatus.RUNNING,
            current_stage=JobStage.VERIFICATION,
            progress=10,
        )
        session.add(pj)
        session.commit()

    with pg_session_factory() as session:
        JobEventService.emit_event(
            session,
            job_id=job_id,
            stage=JobStage.VERIFICATION,
            status=JobStatus.RUNNING,
            progress=20,
            message="Temporary uncommitted event",
        )
        session.rollback()

    with pg_session_factory() as session:
        count = session.query(JobEvent).filter_by(job_id=job_id).count()
        assert count == 0


def test_pg_ambiguous_lock_recovery_fails_closed(pg_session_factory):
    """Verifies that an orphaned/ambiguous lock with missing linked job/run fails closed and is NOT silently removed."""
    res_id = f"b-ambig-{uuid.uuid4()}"
    missing_job_id = f"missing-job-{uuid.uuid4()}"

    with pg_session_factory() as session:
        orphan_lock = ActiveOperationLock(
            resource_type="BIDDER",
            resource_id=res_id,
            operation="VERIFY_BIDDER",
            job_id=missing_job_id,
            owner_principal_id="p-orphan",
        )
        session.add(orphan_lock)
        session.commit()

    with pg_session_factory() as session:
        with pytest.raises(HTTPException) as exc_info:
            OperationLockService.acquire_lock(
                session,
                resource_type="BIDDER",
                resource_id=res_id,
                operation="VERIFY_BIDDER",
                job_id=f"new-job-{uuid.uuid4()}",
                principal_id="p-new",
            )
        assert exc_info.value.status_code == 409
        assert exc_info.value.detail["code"] == "OPERATION_LOCK_RECOVERY_REQUIRED"

    # Verify lock remains intact and was not deleted
    with pg_session_factory() as session:
        remaining_lock = session.query(ActiveOperationLock).filter_by(resource_id=res_id).first()
        assert remaining_lock is not None
        assert remaining_lock.job_id == missing_job_id
        session.delete(remaining_lock)
        session.commit()


def test_pg_completed_operation_response_recovery(pg_session_factory):
    """Verifies that an ActiveOperationLock belonging to a completed job is cleanly reconciled for a new operation."""
    res_id = f"b-comp-{uuid.uuid4()}"
    completed_job_id = f"comp-job-{uuid.uuid4()}"
    new_job_id = f"new-job-{uuid.uuid4()}"

    with pg_session_factory() as session:
        comp_job = ProcessingJob(
            id=completed_job_id,
            target_type="BIDDER",
            target_id=res_id,
            job_type="VERIFY_BIDDER",
            status=JobStatus.COMPLETED,
            current_stage=JobStage.REPORTING,
            progress=100,
        )
        stale_lock = ActiveOperationLock(
            resource_type="BIDDER",
            resource_id=res_id,
            operation="VERIFY_BIDDER",
            job_id=completed_job_id,
            owner_principal_id="p-old",
        )
        session.add(comp_job)
        session.add(stale_lock)
        session.commit()

    with pg_session_factory() as session:
        new_lock = OperationLockService.acquire_lock(
            session,
            resource_type="BIDDER",
            resource_id=res_id,
            operation="VERIFY_BIDDER",
            job_id=new_job_id,
            principal_id="p-new",
        )
        assert new_lock is not None
        assert new_lock.job_id == new_job_id

    # Cleanup
    with pg_session_factory() as session:
        OperationLockService.release_lock(session, "BIDDER", res_id, "VERIFY_BIDDER", new_job_id)
