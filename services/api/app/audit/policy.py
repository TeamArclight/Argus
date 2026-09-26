from typing import Any
from app.core.config import settings


def is_action_eligible_for_anchoring(action: str) -> bool:
    """Checks whether a given audit action is on the critical allowlist for blockchain anchoring."""
    if not action:
        return False
    allowed = settings.get_blockchain_anchor_actions()
    return action.strip() in allowed


def determine_initial_blockchain_status(action: str) -> str:
    """Determines initial blockchain status for a newly created AuditEvent.
    
    If blockchain anchoring is enabled in settings and the action is in the allowlist,
    marks the event as 'PENDING' so the background worker picks it up post-commit.
    Otherwise, marks it as 'NOT_ANCHORED'.
    """
    if settings.BLOCKCHAIN_ENABLED and is_action_eligible_for_anchoring(action):
        return "PENDING"
    return "NOT_ANCHORED"
