from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.auth.dependencies import require_roles
from app.db.session import get_db
from app.models.domain import AuditEvent
from app.schemas.canonical import AuthenticatedPrincipal, UserRole

router = APIRouter(tags=["Audit"])


@router.get("/audit/events")
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

    events = query.order_by(AuditEvent.timestamp.desc()).limit(limit).all()
    return events
