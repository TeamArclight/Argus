from datetime import datetime, timezone
import uuid
from typing import Any
from pydantic import ValidationError
from app.schemas.canonical import (
    AIServiceResult,
    OperatorEnum,
    RequirementType,
    TenderRequirementCreate,
)


class AIServiceAdapter:
    """Interface for intelligence service (document parsing & LLM requirement extraction).
    
    The backend validates all extracted payloads against canonical Pydantic models.
    Does NOT invoke LLMs directly inside backend; interfaces via structured service calls.
    """

    async def extract_tender(
        self, tender_id: str, document_uri: str
    ) -> AIServiceResult:
        """Mock extraction of tender requirements from tender document."""
        if not document_uri:
            return AIServiceResult(
                success=False,
                data=None,
                error_code="INVALID_DOCUMENT_URI",
                retryable=False,
                message="Tender document URI cannot be empty.",
            )

        if "corrupt" in document_uri.lower():
            return AIServiceResult(
                success=False,
                data=None,
                error_code="SCHEMA_VALIDATION_FAILED",
                retryable=True,
                message="Extracted requirement JSON failed Pydantic schema validation.",
            )

        if "unavailable" in document_uri.lower():
            return AIServiceResult(
                success=False,
                data=None,
                error_code="SERVICE_UNAVAILABLE",
                retryable=True,
                message="AI intelligence gateway unavailable.",
            )

        # Standard simulated successful extraction
        sample_requirements = [
            TenderRequirementCreate(
                clause="3.1",
                requirement_type=RequirementType.TURNOVER,
                field="financial.average_annual_turnover",
                operator=OperatorEnum.GTE,
                expected_value=100000000,
                unit="INR",
                mandatory=True,
                source_page=12,
                source_text="The bidder must have a minimum average annual turnover of Rs 10 Crore in the last 3 fiscal years.",
                confidence=0.98,
                requires_verification=True,
            ),
            TenderRequirementCreate(
                clause="3.4",
                requirement_type=RequirementType.BLACK_LIST,
                field="debarment.status",
                operator=OperatorEnum.EQ,
                expected_value=False,
                mandatory=True,
                source_page=15,
                source_text="The bidder must not be debarred or blacklisted by any Government entity.",
                confidence=0.99,
                requires_verification=True,
            ),
            TenderRequirementCreate(
                clause="4.1",
                requirement_type=RequirementType.GST,
                field="general.gstin",
                operator=OperatorEnum.EXISTS,
                expected_value=True,
                mandatory=True,
                source_page=18,
                source_text="Valid GST Registration Certificate required.",
                confidence=0.95,
                requires_verification=True,
            ),
        ]

        return AIServiceResult(
            success=True,
            data=[req.model_dump() for req in sample_requirements],
            error_code=None,
            retryable=False,
            message="Successfully extracted 3 eligibility requirements.",
        )

    async def extract_document(
        self, document_id: str, document_uri: str, bidder_id: str
    ) -> AIServiceResult:
        """Mock extraction of bidder claims/facts from bidder documents."""
        if not document_uri:
            return AIServiceResult(
                success=False,
                data=None,
                error_code="INVALID_DOCUMENT_URI",
                retryable=False,
                message="Document URI missing.",
            )

        sample_facts = [
            {
                "field": "financial.average_annual_turnover",
                "value": 150000000,
                "source_page": 4,
                "source_text": "Average turnover for FY 2022-25 is INR 15,00,00,000 as certified by CA.",
                "confidence": 0.96,
            },
            {
                "field": "debarment.status",
                "value": False,
                "source_page": 8,
                "source_text": "Undertaking: We hereby certify we are not blacklisted.",
                "confidence": 0.99,
            },
            {
                "field": "general.gstin",
                "value": "27AAAAA0000A1Z5",
                "source_page": 1,
                "source_text": "GSTIN: 27AAAAA0000A1Z5",
                "confidence": 0.99,
            },
        ]

        return AIServiceResult(
            success=True,
            data=sample_facts,
            error_code=None,
            retryable=False,
            message="Successfully extracted 3 facts from bidder document.",
        )
