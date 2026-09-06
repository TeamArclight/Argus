from fastapi import APIRouter, Depends
from app.auth.dependencies import get_current_principal
from app.schemas.canonical import AuthenticatedPrincipal

router = APIRouter(tags=["Auth"])


@router.get("/auth/me", response_model=AuthenticatedPrincipal)
def get_current_user_profile(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> AuthenticatedPrincipal:
    """Returns profile and active role of the currently authenticated principal."""
    return principal
