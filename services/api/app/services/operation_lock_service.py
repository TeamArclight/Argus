from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.domain import ActiveOperationLock, ComplianceRun, ProcessingJob
from app.schemas.canonical import JobStatus


class OperationLockService:
    """Service for managing exclusive database-enforced active operation locks."""

    @classmethod
    def acquire_lock(
        cls,
        db: Session,
        resource_type: str,
        resource_id: str,
        operation: str,
        job_id: str,
        principal_id: str,
        run_id: str | None = None,
    ) -> ActiveOperationLock:
        """Acquires an exclusive active-operation lock for the given resource and operation.

        Distinguishes:
        - Own reservation: lock already held by current job_id -> returns existing lock
        - Active operation: linked job/run is QUEUED or RUNNING -> 409 OPERATION_IN_PROGRESS
        - Completed operation: linked job/run is COMPLETED or REVIEW_REQUIRED -> reconciles stale lock
        - Failed operation: linked job/run is FAILED -> reconciles stale lock
        - Ambiguous/Interrupted operation: linked job/run missing or in non-terminal state -> fails closed (409 OPERATION_LOCK_RECOVERY_REQUIRED)
        """
        bind = db.get_bind()
        query = db.query(ActiveOperationLock).filter(
            ActiveOperationLock.resource_type == resource_type,
            ActiveOperationLock.resource_id == resource_id,
            ActiveOperationLock.operation == operation,
        )
        if bind is not None and bind.dialect.name == "postgresql":
            query = query.with_for_update()

        existing_lock = query.first()

        if existing_lock:
            # 1. Own reservation in current transaction/job
            if existing_lock.job_id == job_id:
                return existing_lock

            linked_job = db.query(ProcessingJob).filter(ProcessingJob.id == existing_lock.job_id).first()
            linked_run = (
                db.query(ComplianceRun).filter(ComplianceRun.id == existing_lock.run_id).first()
                if existing_lock.run_id
                else None
            )

            # 2. Check active states
            is_job_active = linked_job is not None and linked_job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
            is_run_active = linked_run is not None and linked_run.execution_status in (JobStatus.QUEUED, JobStatus.RUNNING)

            if is_job_active or is_run_active:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "OPERATION_IN_PROGRESS",
                        "message": f"An active {operation} operation is already in progress for {resource_type} {resource_id}.",
                        "details": {
                            "resource_type": resource_type,
                            "resource_id": resource_id,
                            "operation": operation,
                            "active_job_id": existing_lock.job_id,
                            "active_run_id": existing_lock.run_id,
                        },
                    },
                )

            # 3. Check definitive terminal states
            is_job_completed = linked_job is not None and linked_job.status in (JobStatus.COMPLETED, JobStatus.REVIEW_REQUIRED)
            is_run_completed = linked_run is not None and linked_run.execution_status == JobStatus.COMPLETED
            is_job_failed = linked_job is not None and linked_job.status == JobStatus.FAILED
            is_run_failed = linked_run is not None and linked_run.execution_status == JobStatus.FAILED

            is_terminal = is_job_completed or is_run_completed or is_job_failed or is_run_failed

            if is_terminal:
                # Atomically delete stale terminal lock within session
                db.delete(existing_lock)
                db.flush()
            else:
                # 4. Ambiguous / Missing / Interrupted durable state -> FAIL CLOSED
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "code": "OPERATION_LOCK_RECOVERY_REQUIRED",
                        "message": f"Active operation lock for {resource_type} {resource_id} is in an ambiguous or unverified state. Linked job/run was not cleanly terminated. Manual administrative recovery required before acquiring a new lock.",
                        "details": {
                            "resource_type": resource_type,
                            "resource_id": resource_id,
                            "operation": operation,
                            "lock_id": existing_lock.id,
                            "job_id": existing_lock.job_id,
                            "run_id": existing_lock.run_id,
                        },
                    },
                )

        active_job = (
            db.query(ProcessingJob)
            .filter(
                ProcessingJob.target_type == resource_type,
                ProcessingJob.target_id == resource_id,
                ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .first()
        )
        if active_job and active_job.id != job_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "OPERATION_IN_PROGRESS",
                    "message": f"An active {operation} operation is already in progress for {resource_type} {resource_id}.",
                    "details": {
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "operation": operation,
                        "active_job_id": active_job.id,
                    },
                },
            )

        lock = ActiveOperationLock(
            resource_type=resource_type,
            resource_id=resource_id,
            operation=operation,
            job_id=job_id,
            run_id=run_id,
            owner_principal_id=principal_id,
        )
        try:
            db.add(lock)
            db.commit()
            db.refresh(lock)
            return lock
        except IntegrityError:
            db.rollback()
            winner_lock = (
                db.query(ActiveOperationLock)
                .filter(
                    ActiveOperationLock.resource_type == resource_type,
                    ActiveOperationLock.resource_id == resource_id,
                    ActiveOperationLock.operation == operation,
                )
                .first()
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "OPERATION_IN_PROGRESS",
                    "message": f"Concurrent active {operation} operation in progress for {resource_type} {resource_id}.",
                    "details": {
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                        "operation": operation,
                        "active_job_id": winner_lock.job_id if winner_lock else None,
                    },
                },
            )

    @classmethod
    def release_lock(
        cls,
        db: Session,
        resource_type: str,
        resource_id: str,
        operation: str,
        job_id: str,
        run_id: str | None = None,
    ) -> bool:
        """Releases the active operation lock only if it matches the expected job_id / run_id.
        
        A missing or mismatched job_id is strictly rejected and will not release any locks.
        """
        if not job_id or not job_id.strip():
            raise ValueError("job_id is required to release an active operation lock.")

        query = db.query(ActiveOperationLock).filter(
            ActiveOperationLock.resource_type == resource_type,
            ActiveOperationLock.resource_id == resource_id,
            ActiveOperationLock.operation == operation,
            ActiveOperationLock.job_id == job_id.strip(),
        )
        if run_id is not None and run_id.strip():
            query = query.filter(ActiveOperationLock.run_id == run_id.strip())

        bind = db.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            query = query.with_for_update()

        lock = query.first()
        if lock:
            db.delete(lock)
            db.commit()
            return True
        return False
