from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models.domain import ActiveOperationLock, ProcessingJob
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
        Raises HTTPException(409, OPERATION_IN_PROGRESS) if already locked by an active operation.
        """
        existing_lock = (
            db.query(ActiveOperationLock)
            .filter(
                ActiveOperationLock.resource_type == resource_type,
                ActiveOperationLock.resource_id == resource_id,
                ActiveOperationLock.operation == operation,
            )
            .first()
        )
        if existing_lock:
            from app.models.domain import ComplianceRun
            from app.audit.logger import AuditLogger

            linked_job = db.query(ProcessingJob).filter(ProcessingJob.id == existing_lock.job_id).first()
            linked_run = (
                db.query(ComplianceRun).filter(ComplianceRun.id == existing_lock.run_id).first()
                if existing_lock.run_id
                else None
            )

            is_job_active = linked_job and linked_job.status in (JobStatus.QUEUED, JobStatus.RUNNING)
            is_run_active = linked_run and linked_run.execution_status == JobStatus.RUNNING

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

            # Reconcile terminal state or orphaned lock
            if linked_job is None and linked_run is None:
                AuditLogger.log(
                    db,
                    action="ORPHAN_LOCK_RECONCILED",
                    entity_type=resource_type,
                    entity_id=resource_id,
                    actor_id=principal_id,
                    payload={
                        "reconciled_lock_id": existing_lock.id,
                        "orphan_job_id": existing_lock.job_id,
                        "operation": operation,
                    },
                )
            db.delete(existing_lock)
            db.commit()

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
        job_id: str | None = None,
    ) -> None:
        """Releases the active operation lock."""
        query = db.query(ActiveOperationLock).filter(
            ActiveOperationLock.resource_type == resource_type,
            ActiveOperationLock.resource_id == resource_id,
            ActiveOperationLock.operation == operation,
        )
        if job_id:
            query = query.filter(ActiveOperationLock.job_id == job_id)
        lock = query.first()
        if lock:
            db.delete(lock)
            db.commit()
