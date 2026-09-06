from datetime import datetime, timedelta, timezone
from typing import Any
import jwt
from app.core.config import settings
from app.schemas.canonical import AuthenticatedPrincipal, UserRole


def create_access_token(
    user_id: str,
    role: UserRole | str,
    name: str | None = None,
    email: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Generates a signed JWT access token containing standard claims and role assignment."""
    if isinstance(role, UserRole):
        role_str = role.value
    else:
        role_str = str(role)

    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ARGUS_JWT_ACCESS_TOKEN_MINUTES)

    payload: dict[str, Any] = {
        "sub": user_id,
        "role": role_str,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
        "iss": settings.ARGUS_JWT_ISSUER,
        "aud": settings.ARGUS_JWT_AUDIENCE,
    }
    if name:
        payload["name"] = name
    if email:
        payload["email"] = email

    token = jwt.encode(
        payload,
        settings.ARGUS_JWT_SECRET,
        algorithm=settings.ARGUS_JWT_ALGORITHM,
    )
    return token


def decode_access_token(token: str) -> AuthenticatedPrincipal:
    """Decodes and strictly validates JWT signature, claims, expiration, issuer, audience, and role."""
    try:
        payload = jwt.decode(
            token,
            settings.ARGUS_JWT_SECRET,
            algorithms=[settings.ARGUS_JWT_ALGORITHM],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": True,
                "require": ["sub", "role", "exp", "iss", "aud"],
            },
            issuer=settings.ARGUS_JWT_ISSUER,
            audience=settings.ARGUS_JWT_AUDIENCE,
        )
    except jwt.PyJWTError as e:
        raise ValueError(f"Invalid token: {e}") from e

    sub = payload.get("sub")
    if not sub or not isinstance(sub, str) or not sub.strip():
        raise ValueError("Invalid token claim: 'sub' must be a non-empty string.")

    role_val = payload.get("role")
    try:
        user_role = UserRole(role_val)
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"Invalid token claim: 'role' '{role_val}' is not a valid UserRole.") from e

    return AuthenticatedPrincipal(
        user_id=sub,
        name=payload.get("name"),
        role=user_role,
        email=payload.get("email"),
    )
