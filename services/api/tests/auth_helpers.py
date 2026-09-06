from app.auth.tokens import create_access_token
from app.schemas.canonical import UserRole


def get_auth_headers(
    role: UserRole = UserRole.ADMIN,
    user_id: str = "test-user-001",
    name: str | None = "Test User",
) -> dict[str, str]:
    """Generates valid Authorization bearer headers for test HTTP requests."""
    token = create_access_token(user_id=user_id, role=role, name=name)
    return {"Authorization": f"Bearer {token}"}
