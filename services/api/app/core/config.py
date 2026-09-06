import os
from typing import Any
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.schemas.canonical import VerificationMode


class Settings(BaseSettings):
    """ARGUS Platform Configuration Settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    APP_ENV: str = "development"
    DATABASE_URL: str = "sqlite:///./argus_dev.db"

    # Core Verification Defaults (Default to LIVE mode per governance policy)
    DEFAULT_VERIFICATION_MODE: VerificationMode = VerificationMode.LIVE

    # Per-Registry Verification Modes (None inherits DEFAULT_VERIFICATION_MODE)
    GST_VERIFICATION_MODE: VerificationMode | None = None
    GST_API_BASE_URL: str | None = None
    GST_API_KEY: str | None = None

    UDYAM_VERIFICATION_MODE: VerificationMode | None = None
    UDYAM_API_BASE_URL: str | None = None
    UDYAM_API_KEY: str | None = None

    MCA_VERIFICATION_MODE: VerificationMode | None = None
    MCA_API_BASE_URL: str | None = None
    MCA_API_KEY: str | None = None

    EPFO_VERIFICATION_MODE: VerificationMode | None = None
    EPFO_API_BASE_URL: str | None = None
    EPFO_API_KEY: str | None = None

    ESIC_VERIFICATION_MODE: VerificationMode | None = None
    ESIC_API_BASE_URL: str | None = None
    ESIC_API_KEY: str | None = None

    BLACKLIST_VERIFICATION_MODE: VerificationMode | None = None
    BLACKLIST_API_BASE_URL: str | None = None
    BLACKLIST_API_KEY: str | None = None

    # ARGUS Intelligence Gateway
    ARGUS_INTELLIGENCE_BASE_URL: str | None = None
    ARGUS_INTELLIGENCE_API_KEY: str | None = None

    REQUEST_TIMEOUT_SECONDS: float = 10.0
    RUN_REAL_INTEGRATION_TESTS: bool = False

    def get_mode_for_domain(self, domain: str) -> VerificationMode:
        """Resolves active verification mode for a given domain (e.g. 'gst', 'udyam')."""
        env_attr = f"{domain.upper()}_VERIFICATION_MODE"
        specific_mode = getattr(self, env_attr, None)
        if specific_mode:
            return specific_mode

        # Domain-specific default rules if unconfigured
        if domain.lower() in ("epfo", "esic"):
            return VerificationMode.DOCUMENT

        return self.DEFAULT_VERIFICATION_MODE


settings = Settings()
