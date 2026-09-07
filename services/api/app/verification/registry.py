from pathlib import Path
from typing import Any
from app.core.config import settings
from app.schemas.canonical import (
    ProviderConfigurationStatus,
    ProviderHealthRead,
    ProviderOperationalHealth,
    VerificationMode,
)


class ProviderRegistry:
    """Registry managing domain verification providers, capabilities, configuration, and health metadata."""

    _DOMAINS = {
        "gst": {
            "name": "GST Official Registry",
            "supported_fields": ["general.gstin", "financial.turnover"],
            "capabilities": ["GSTIN Format Validation", "GST Active Registration Lookup", "Turnover Verification"],
        },
        "udyam": {
            "name": "MSME Udyam Portal",
            "supported_fields": ["general.udyam", "general.enterprise_type"],
            "capabilities": ["Udyam Registration Format Validation", "MSME Category Verification"],
        },
        "mca": {
            "name": "Ministry of Corporate Affairs (MCA)",
            "supported_fields": ["general.cin", "legal.company_status"],
            "capabilities": ["CIN Corporate Lookup", "Company Active Status Verification"],
        },
        "epfo": {
            "name": "EPFO Labor Compliance Registry",
            "supported_fields": ["general.epfo", "compliance.epfo_status"],
            "capabilities": ["EPF Establishment Search", "Labor Compliance Status"],
        },
        "esic": {
            "name": "ESIC Medical & Social Security Portal",
            "supported_fields": ["general.esic", "compliance.esic_status"],
            "capabilities": ["ESIC Code Search", "Employer Security Verification"],
        },
        "blacklist": {
            "name": "GeM & Public Procurement Debarment Registry",
            "supported_fields": ["debarment.status", "legal.debarment"],
            "capabilities": ["Central Debarment Search", "Public Procurement Blacklist Lookup"],
        },
    }

    @classmethod
    def get_provider_health_list(cls) -> list[ProviderHealthRead]:
        """Returns health and configuration metadata for all registered domain verification providers without making live external HTTP pings."""
        results: list[ProviderHealthRead] = []

        for domain, info in cls._DOMAINS.items():
            mode = settings.get_mode_for_domain(domain)
            api_url = getattr(settings, f"{domain.upper()}_API_URL", None) or getattr(settings, f"{domain.upper()}_API_BASE_URL", None)
            api_key = getattr(settings, f"{domain.upper()}_API_KEY", None)

            last_checked: datetime | None = None

            if mode == VerificationMode.LIVE:
                configured = bool(api_url and api_key)
                config_status = ProviderConfigurationStatus.CONFIGURED if configured else ProviderConfigurationStatus.UNCONFIGURED
                health_status = ProviderOperationalHealth.UNKNOWN if configured else ProviderOperationalHealth.UNAVAILABLE
                notes = (
                    "Live provider configured; operational health is UNKNOWN until active check."
                    if configured
                    else "Live provider unconfigured; missing API URL or key."
                )
            elif mode == VerificationMode.PORTAL_CACHED:
                config_status = ProviderConfigurationStatus.CONFIGURED
                cache_dir = getattr(settings, "PORTAL_CACHE_DIR", None) or getattr(settings, f"{domain.upper()}_CACHE_DIR", None)
                if cache_dir and Path(cache_dir).exists():
                    health_status = ProviderOperationalHealth.AVAILABLE
                    notes = "Portal verified cached dataset present and readable."
                else:
                    health_status = ProviderOperationalHealth.UNKNOWN
                    notes = "Portal cache mode configured; dataset presence unverified."
            elif mode == VerificationMode.DOCUMENT:
                config_status = ProviderConfigurationStatus.CONFIGURED
                health_status = ProviderOperationalHealth.UNKNOWN
                notes = "Document extraction mode configured; operational health unverified."
            else:  # DEMO
                config_status = ProviderConfigurationStatus.CONFIGURED
                health_status = ProviderOperationalHealth.AVAILABLE
                notes = "Deterministic demonstration provider active (DEMO mode)."

            results.append(
                ProviderHealthRead(
                    provider_identifier=domain,
                    domain=info["name"],
                    configured_mode=mode,
                    configuration_status=config_status,
                    operational_health=health_status,
                    supported_fields=info["supported_fields"],
                    capabilities=info["capabilities"],
                    last_checked_at=last_checked,
                    notes=notes,
                )
            )

        return results
