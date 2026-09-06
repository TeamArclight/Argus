from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid
from typing import Any
from app.schemas.canonical import (
    VerificationMode,
    VerificationResultRead,
    VerificationSource,
    VerificationStatus,
)
from app.verification.providers.base import BaseVerificationProvider

FIXTURES_PATH = Path(__file__).parent.parent / "fixtures" / "portal_cache.json"


def normalize_company_name(name: str) -> str:
    """Normalizes company name for fuzzy portal matching."""
    name = name.upper()
    name = re.sub(r"\bPVT\.?\b|\bPRIVATE\b", "", name)
    name = re.sub(r"\bLTD\.?\b|\bLIMITED\b", "", name)
    name = re.sub(r"[^\w\s]", "", name)
    return " ".join(name.split())


class PortalCachedProvider(BaseVerificationProvider):
    """Provider reading structured cached records from official portal verifications."""

    def __init__(self, domain: str, source: VerificationSource):
        self.domain = domain
        self._source = source
        self._cache_data: dict[str, Any] = self._load_cache()

    @property
    def mode(self) -> VerificationMode:
        return VerificationMode.PORTAL_CACHED

    @property
    def source(self) -> VerificationSource:
        return self._source

    def _load_cache(self) -> dict[str, Any]:
        if not FIXTURES_PATH.exists():
            return {}
        try:
            with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get(self.domain, {})
        except Exception:
            return {}

    async def verify(
        self, bidder_data: dict[str, Any], field: str
    ) -> VerificationResultRead:
        now = datetime.now(timezone.utc)
        ver_id = str(uuid.uuid4())
        bidder_id = bidder_data.get("id", "UNKNOWN_BIDDER")

        # Determine identifier key to search based on domain
        key_map = {
            "gst": "gstin",
            "udyam": "udyam_number",
            "mca": "cin",
            "epfo": "pan",
            "esic": "pan",
            "blacklist": "bidder_name",
        }

        attr = key_map.get(self.domain, "bidder_name")
        identifier = bidder_data.get(attr) or bidder_data.get("bidder_name")

        if not identifier:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=None,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=f"Missing identifier parameter '{attr}' for portal cache lookup.",
            )

        cached_entry = self._cache_data.get(identifier)

        if not cached_entry:
            return VerificationResultRead(
                id=ver_id,
                bidder_id=bidder_id,
                field=field,
                claimed_value=identifier,
                verified_value=None,
                status=VerificationStatus.UNVERIFIED,
                source=self.source,
                mode=self.mode,
                checked_at=now,
                error_message=f"No portal cache record found for identifier '{identifier}'.",
            )

        # Check for entity name mismatch in cached record
        claimed_entity = bidder_data.get("bidder_name", "")
        verified_entity = cached_entry.get("verified_entity") or cached_entry.get("company_name") or ""

        status = VerificationStatus.VERIFIED
        err_msg = None

        if verified_entity and claimed_entity:
            norm_claimed = normalize_company_name(claimed_entity)
            norm_verified = normalize_company_name(verified_entity)

            # If names differ significantly and one is not contained in the other
            if "MISMATCH" in verified_entity or (norm_claimed and norm_verified and norm_claimed not in norm_verified and norm_verified not in norm_claimed):
                status = VerificationStatus.MISMATCH
                err_msg = f"Claimed entity '{claimed_entity}' does not match portal cached record '{verified_entity}'"

        return VerificationResultRead(
            id=ver_id,
            bidder_id=bidder_id,
            field=field,
            claimed_value=identifier,
            verified_value=cached_entry,
            status=status,
            source=self.source,
            mode=self.mode,
            checked_at=now,
            verification_reference=cached_entry.get("reference"),
            error_message=err_msg,
        )
