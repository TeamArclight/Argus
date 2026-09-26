from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.auth.dependencies import require_roles
from app.db.session import get_db
from app.models.domain import AuditEvent
from app.schemas.canonical import (
    AuditEventBlockchainRead,
    AuditEventIntegrityVerifyResponse,
    AuditEventRead,
    AuthenticatedPrincipal,
    UserRole,
)

router = APIRouter(tags=["Audit"])


@router.get("/audit/events", response_model=list[AuditEventRead])
def list_audit_events(
    entity_type: str | None = Query(None, description="Filter by entity type (e.g. BIDDER, TENDER)"),
    entity_id: str | None = Query(None, description="Filter by entity ID"),
    action: str | None = Query(None, description="Filter by action name"),
    limit: int = Query(50, ge=1, le=500),
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.AUDITOR, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Returns audit log records matching specified entity/action filters."""
    query = db.query(AuditEvent)
    if entity_type:
        query = query.filter(AuditEvent.entity_type == entity_type)
    if entity_id:
        query = query.filter(AuditEvent.entity_id == entity_id)
    if action:
        query = query.filter(AuditEvent.action == action)

    events = query.order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc()).limit(limit).all()
    return events


@router.get("/audit/{event_id}/blockchain", response_model=AuditEventBlockchainRead)
def get_audit_event_blockchain(
    event_id: str,
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.AUDITOR, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Retrieves on-chain anchoring status and transaction metadata for an AuditEvent."""
    from fastapi import HTTPException, status
    from app.core.config import settings

    event = db.query(AuditEvent).filter(AuditEvent.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AuditEvent with ID {event_id} not found.",
        )

    explorer_url = None
    if event.blockchain_tx_hash and settings.BLOCKCHAIN_EXPLORER_URL:
        clean_tx = event.blockchain_tx_hash if event.blockchain_tx_hash.startswith("0x") else f"0x{event.blockchain_tx_hash}"
        explorer_url = f"{settings.BLOCKCHAIN_EXPLORER_URL.rstrip('/')}/tx/{clean_tx}"

    return AuditEventBlockchainRead(
        event_id=event.id,
        event_hash=event.event_hash,
        blockchain_status=event.blockchain_status or "NOT_ANCHORED",
        network=event.blockchain_network or "polygon-amoy",
        chain_id=settings.BLOCKCHAIN_CHAIN_ID,
        transaction_hash=event.blockchain_tx_hash,
        block_number=event.blockchain_block_number,
        anchored_at=event.anchored_at,
        explorer_url=explorer_url,
        audit_hash_version=event.audit_hash_version or "v1",
        blockchain_error=event.blockchain_error,
    )


@router.post("/audit/{event_id}/verify", response_model=AuditEventIntegrityVerifyResponse)
def verify_audit_event_integrity(
    event_id: str,
    principal: AuthenticatedPrincipal = Depends(
        require_roles(UserRole.ADMIN, UserRole.AUDITOR, UserRole.PROCUREMENT_OFFICER)
    ),
    db: Session = Depends(get_db),
):
    """Recomputes canonical SHA-256 hash from PostgreSQL and verifies against on-chain smart contract anchor."""
    from fastapi import HTTPException, status
    from app.services.blockchain_service import BlockchainAuditService

    event = db.query(AuditEvent).filter(AuditEvent.id == event_id).first()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AuditEvent with ID {event_id} not found.",
        )

    service = BlockchainAuditService()
    res = service.verify_event_integrity(db, event_id)
    return AuditEventIntegrityVerifyResponse(**res)
