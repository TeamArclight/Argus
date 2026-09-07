from datetime import datetime, timezone
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
            "capabilities": ["GSTIN Validation", "Active Registration Check", "Tax Filing Status"],
        },
        "udyam": {
            "name": "MSME Udyam Portal",
            "supported_fields": ["general.udyam", "general.enterprise_type"],
            "capabilities": ["Udyam Registration Validation", "MSME Category Verification"],
        },
        "mca": {
            "name": "Ministry of Corporate Affairs (MCA)",
            "supported_fields": ["general.cin", "legal.company_status"],
            "capabilities": ["CIN Corporate Lookup", "Company Active Status", "Director Master Check"],
        },
        "epfo": {
            "name": "EPFO Labor Compliance Registry",
            "supported_fields": ["general.epfo", "compliance.epfo_status"],
            "capabilities": ["EPF Establishment Search", "Challan Payment History", "Labor Compliance"],
        },
        "esic": {
            "name": "ESIC Medical & Social Security Portal",
            "supported_fields": ["general.esic", "compliance.esic_status"],
            "capabilities": ["ESIC Code Search", "Employer Contribution Verification"],
        },
        "blacklist": {
            "name": "GeM & Public Procurement Debarment Registry",
            "supported_fields": ["debarment.status", "legal.debarment"],
            "capabilities": ["Central Debarment Search", "GeM Blacklist Verification", "State Ban Lookup"],
        },
    }

    @classmethod
    def get_provider_health_list(cls) -> list[ProviderHealthRead]:
        """Returns health and configuration metadata for all registered domain verification providers without making external HTTP pings."""
        now = datetime.now(timezone.utc)
        results: list[ProviderHealthRead] = []

        for domain, info in cls._DOMAINS.items():
            mode = settings.get_mode_for_domain(domain)
            api_url = getattr(settings, f"{domain.upper()}_API_URL", None) or getattr(settings, f"{domain.upper()}_API_BASE_URL", None)
            api_key = getattr(settings, f"{domain.upper()}_API_KEY", None)

            if mode == VerificationMode.LIVE:
                configured = bool(api_url and api_key)
                config_status = ProviderConfigurationStatus.CONFIGURED if configured else ProviderConfigurationStatus.UNCONFIGURED
                health_status = ProviderOperationalHealth.UNKNOWN if configured else ProviderOperationalHealth.UNAVAILABLE
                notes = (
                    "Live authorized gateway configured; external health is unverified until live API request."
                    if configured
                    else "Live provider is unconfigured; missing API URL or authorization key."
                )
            elif mode == VerificationMode.PORTAL_CACHED:
                config_status = ProviderConfigurationStatus.CONFIGURED
                health_status = ProviderOperationalHealth.AVAILABLE
                notes = "Portal verified cached dataset active."
            elif mode == VerificationMode.DOCUMENT:
                config_status = ProviderConfigurationStatus.CONFIGURED
                health_status = ProviderOperationalHealth.AVAILABLE
                notes = "Submitted document extraction & verification provider active."
            else:  # DEMO
                config_status = ProviderConfigurationStatus.CONFIGURED
                health_status = ProviderOperationalHealth.AVAILABLE
                notes = "Curated deterministic SIH demonstration provider active (DEMO mode)."

            results.append(
                ProviderHealthRead(
                    provider_identifier=domain,
                    domain=info["name"],
                    configured_mode=mode,
                    configuration_status=config_status,
                    operational_health=health_status,
                    supported_fields=info["supported_fields"],
                    capabilities=info["capabilities"],
                    last_checked_at=now,
                    notes=notes,
                )
            )

        return results
