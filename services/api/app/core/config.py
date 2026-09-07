from typing import Any, Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.schemas.canonical import AuthMode, VerificationMode


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

    # Per-Registry Verification Modes and Endpoints
    GST_VERIFICATION_MODE: VerificationMode | None = None
    GST_API_URL: str | None = None
    GST_API_BASE_URL: str | None = None
    GST_AUTH_MODE: AuthMode = AuthMode.BEARER
    GST_API_KEY: str | None = None

    UDYAM_VERIFICATION_MODE: VerificationMode | None = None
    UDYAM_API_URL: str | None = None
    UDYAM_API_BASE_URL: str | None = None
    UDYAM_AUTH_MODE: AuthMode = AuthMode.BEARER
    UDYAM_API_KEY: str | None = None

    MCA_VERIFICATION_MODE: VerificationMode | None = None
    MCA_API_URL: str | None = None
    MCA_API_BASE_URL: str | None = None
    MCA_AUTH_MODE: AuthMode = AuthMode.BEARER
    MCA_API_KEY: str | None = None

    EPFO_VERIFICATION_MODE: VerificationMode | None = None
    EPFO_API_URL: str | None = None
    EPFO_API_BASE_URL: str | None = None
    EPFO_AUTH_MODE: AuthMode = AuthMode.BEARER
    EPFO_API_KEY: str | None = None

    ESIC_VERIFICATION_MODE: VerificationMode | None = None
    ESIC_API_URL: str | None = None
    ESIC_API_BASE_URL: str | None = None
    ESIC_AUTH_MODE: AuthMode = AuthMode.BEARER
    ESIC_API_KEY: str | None = None

    BLACKLIST_VERIFICATION_MODE: VerificationMode | None = None
    BLACKLIST_API_URL: str | None = None
    BLACKLIST_API_BASE_URL: str | None = None
    BLACKLIST_AUTH_MODE: AuthMode = AuthMode.BEARER
    BLACKLIST_API_KEY: str | None = None

    # ARGUS Intelligence Gateway
    ARGUS_INTELLIGENCE_BASE_URL: str | None = None
    ARGUS_INTELLIGENCE_API_KEY: str | None = None
    ARGUS_INTELLIGENCE_EXTRACT_TENDER_URL: str | None = None
    ARGUS_INTELLIGENCE_EXTRACT_DOCUMENT_URL: str | None = None
    ARGUS_INTELLIGENCE_RAG_URL: str | None = None

    REQUEST_TIMEOUT_SECONDS: float = 10.0
    RUN_REAL_INTEGRATION_TESTS: bool = False

    # Security & CORS Config
    CORS_ALLOWED_ORIGINS: str | list[str] = "http://localhost:3000"

    # JWT Authentication & RBAC Configuration (Fails closed if ARGUS_JWT_SECRET is missing)
    ARGUS_JWT_SECRET: str
    ARGUS_JWT_ALGORITHM: Literal["HS256"] = "HS256"
    ARGUS_JWT_ISSUER: str = "argus-api"
    ARGUS_JWT_AUDIENCE: str = "argus-clients"
    ARGUS_JWT_ACCESS_TOKEN_MINUTES: int = 60

    @field_validator("ARGUS_JWT_SECRET")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if not v or not isinstance(v, str) or len(v.strip()) < 32:
            raise ValueError("ARGUS_JWT_SECRET is required, non-empty, and must be at least 32 characters long.")
        return v

    @field_validator("ARGUS_JWT_ISSUER")
    @classmethod
    def validate_jwt_issuer(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("ARGUS_JWT_ISSUER must be a non-empty string.")
        return v.strip()

    @field_validator("ARGUS_JWT_AUDIENCE")
    @classmethod
    def validate_jwt_audience(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("ARGUS_JWT_AUDIENCE must be a non-empty string.")
        return v.strip()

    @field_validator("ARGUS_JWT_ACCESS_TOKEN_MINUTES")
    @classmethod
    def validate_jwt_access_token_minutes(cls, v: int) -> int:
        if not isinstance(v, int) or v <= 0 or v > 525600:
            raise ValueError("ARGUS_JWT_ACCESS_TOKEN_MINUTES must be a positive integer <= 525600.")
        return v

    # Storage Configuration
    ARGUS_STORAGE_BACKEND: Literal["local"] = "local"
    ARGUS_STORAGE_LOCAL_PATH: str = "./data/uploads"
    ARGUS_MAX_UPLOAD_MB: int = 20

    @field_validator("ARGUS_STORAGE_BACKEND")
    @classmethod
    def validate_storage_backend(cls, v: str) -> str:
        if v not in ("local",):
            raise ValueError("ARGUS_STORAGE_BACKEND must be 'local'.")
        return v

    @field_validator("ARGUS_STORAGE_LOCAL_PATH")
    @classmethod
    def validate_storage_local_path(cls, v: str) -> str:
        if not v or not isinstance(v, str) or not v.strip():
            raise ValueError("ARGUS_STORAGE_LOCAL_PATH must be a non-empty string.")
        return v.strip()

    @field_validator("ARGUS_MAX_UPLOAD_MB")
    @classmethod
    def validate_max_upload_mb(cls, v: int) -> int:
        if not isinstance(v, int) or v <= 0 or v > 500:
            raise ValueError("ARGUS_MAX_UPLOAD_MB must be a positive integer <= 500.")
        return v


    def get_cors_origins(self) -> list[str]:
        """Returns list of allowed origins parsed from string or list."""
        if isinstance(self.CORS_ALLOWED_ORIGINS, str):
            return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]
        return self.CORS_ALLOWED_ORIGINS

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

