from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session
from app.auth.dependencies import get_current_principal, require_roles
from app.audit.logger import AuditLogger
from app.db.session import get_db
from app.schemas.canonical import AuthenticatedPrincipal, ProviderHealthRead, UserRole
from app.verification.registry import ProviderRegistry

router = APIRouter(tags=["Providers"])


@router.get("/providers", response_model=list[ProviderHealthRead])
def list_provider_health(
    principal: AuthenticatedPrincipal = Depends(
        require_roles(
            UserRole.ADMIN,
            UserRole.PROCUREMENT_OFFICER,
            UserRole.REVIEWER,
            UserRole.AUDITOR,
        )
    ),
    db: Session = Depends(get_db),
):
    """Returns operational health, configuration status, supported fields, and active mode for all verification providers without external HTTP pings."""
    providers = ProviderRegistry.get_provider_health_list()

    AuditLogger.log(
        db,
        action="PROVIDER_HEALTH_CHECKED",
        entity_type="SYSTEM",
        entity_id="PROVIDERS",
        actor_id=principal.user_id,
        actor_role=principal.role.value,
        payload={"provider_count": len(providers)},
    )

    return providers
