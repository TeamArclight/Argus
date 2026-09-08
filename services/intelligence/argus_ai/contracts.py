"""Intelligence-owned draft contracts.

These deliberately exclude database-owned fields from the API read models.  The
API integration layer is responsible for adding ids and timestamps after it
persists a validated draft.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class RequirementType(str, Enum):
    TURNOVER = "TURNOVER"; EXPERIENCE = "EXPERIENCE"; BLACK_LIST = "BLACK_LIST"
    GST = "GST"; UDYAM = "UDYAM"; EPFO = "EPFO"; ESIC = "ESIC"; MCA = "MCA"; CUSTOM = "CUSTOM"


class Operator(str, Enum):
    EQ = "EQ"; NE = "NE"; GT = "GT"; GTE = "GTE"; LT = "LT"; LTE = "LTE"
    EXISTS = "EXISTS"; NOT_EXISTS = "NOT_EXISTS"; COUNT_GTE = "COUNT_GTE"
    DATE_BEFORE = "DATE_BEFORE"; DATE_AFTER = "DATE_AFTER"; IN = "IN"; NOT_IN = "NOT_IN"


class DocumentType(str, Enum):
    TURNOVER_CERT = "TURNOVER_CERT"; GST_CERT = "GST_CERT"; UDYAM_CERT = "UDYAM_CERT"
    EXPERIENCE_CERT = "EXPERIENCE_CERT"; EPFO_CHALLAN = "EPFO_CHALLAN"; ESIC_CHALLAN = "ESIC_CHALLAN"
    PAN_CERT = "PAN_CERT"; OTHER = "OTHER"


class SourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    page: Optional[int] = Field(default=None, ge=1)
    source_text: str
    bounding_box: Optional[tuple[float, float, float, float]] = None


class TenderRequirementDraft(BaseModel):
    """Maps to TenderRequirementRead once API supplies id/tender_id/created_at."""
    model_config = ConfigDict(extra="forbid")
    clause: str
    requirement_type: RequirementType
    field: str
    operator: Operator
    expected_value: Any
    unit: Optional[str] = None
    mandatory: bool = True
    source_page: Optional[int] = Field(default=None, ge=1)
    source_text: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    requires_verification: bool = False


class ExtractedFactDraft(BaseModel):
    """Maps to FactRead once API supplies id and created_at."""
    model_config = ConfigDict(extra="forbid")
    document_id: str
    bidder_id: str
    field: str
    value: Any
    normalized_value: Optional[Any] = None
    source_page: Optional[int] = Field(default=None, ge=1)
    source_text: Optional[str] = None
    bounding_box: Optional[tuple[float, float, float, float]] = None
    confidence: float = Field(ge=0, le=1)
    provider: str
    model: str


class DocumentClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_type: DocumentType
    confidence: float = Field(ge=0, le=1)
    rationale: str


class EvidenceChunk(BaseModel):
    """Maps to EvidenceRead; content hash/version guard policy freshness."""
    model_config = ConfigDict(extra="forbid")
    id: str
    entity_type: str
    entity_id: str
    snippet: str
    source_uri: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    location_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str
    version: Optional[str] = None
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    security_level: str = "INTERNAL"


class RiskSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    signal_type: str
    severity: str
    title: str
    description: str
    evidence_ids: list[str] = Field(default_factory=list)


class AIServiceResult(BaseModel):
    """Local result envelope pending a shared API schema."""
    model_config = ConfigDict(extra="forbid")
    status: str
    data: Any = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class TenderExtractionResponse(BaseModel):
    """Exact response body consumed by API's TenderRequirementCreate validator."""
    model_config = ConfigDict(extra="forbid")
    requirements: list[TenderRequirementDraft] = Field(default_factory=list)


class DocumentExtractionResponse(BaseModel):
    """Exact response body consumed by API's ExtractedFactCreate validator."""
    model_config = ConfigDict(extra="forbid")
    facts: list[ExtractedFactDraft] = Field(default_factory=list)
