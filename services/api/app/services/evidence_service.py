from datetime import datetime, timezone
import uuid
from typing import Any
from sqlalchemy.orm import Session

from app.models.domain import (
    Bidder,
    ComplianceRun,
    Document,
    Evidence,
    ExtractedFact,
    Tender,
    VerificationResult,
)
from app.schemas.canonical import (
    EvidenceCreate,
    EvidenceRead,
    VerificationMode,
    VerificationStatus,
)


class EvidenceOwnershipError(ValueError):
    """Raised when an evidence linking attempt violates bidder or tender ownership boundaries."""
    pass


class EvidenceNormalizationService:
    """Service for validating evidence ownership and normalizing raw claims and verifications into evidence records."""

    @classmethod
    def validate_ownership(
        cls,
        bidder_id: str,
        tender_id: str,
        document: Document | None = None,
        fact: ExtractedFact | None = None,
        verification: VerificationResult | None = None,
        run: ComplianceRun | None = None,
    ) -> None:
        """Validates that all evidence components strictly belong to the specified bidder and tender."""
        if run:
            if run.bidder_id != bidder_id:
                raise EvidenceOwnershipError(f"ComplianceRun '{run.id}' bidder_id '{run.bidder_id}' does not match target bidder_id '{bidder_id}'.")
            if run.tender_id != tender_id:
                raise EvidenceOwnershipError(f"ComplianceRun '{run.id}' tender_id '{run.tender_id}' does not match target tender_id '{tender_id}'.")

        if document:
            if document.bidder_id and document.bidder_id != bidder_id:
                raise EvidenceOwnershipError(f"Document '{document.id}' bidder_id '{document.bidder_id}' does not match target bidder_id '{bidder_id}'.")
            if document.tender_id and document.tender_id != tender_id:
                raise EvidenceOwnershipError(f"Document '{document.id}' tender_id '{document.tender_id}' does not match target tender_id '{tender_id}'.")

        if fact:
            if fact.bidder_id != bidder_id:
                raise EvidenceOwnershipError(f"ExtractedFact '{fact.id}' bidder_id '{fact.bidder_id}' does not match target bidder_id '{bidder_id}'.")
            if document and fact.document_id != document.id:
                raise EvidenceOwnershipError(f"ExtractedFact '{fact.id}' document_id '{fact.document_id}' does not match Document '{document.id}'.")

        if verification:
            if verification.bidder_id != bidder_id:
                raise EvidenceOwnershipError(f"VerificationResult '{verification.id}' bidder_id '{verification.bidder_id}' does not match target bidder_id '{bidder_id}'.")
            if run and verification.run_id and verification.run_id != run.id:
                raise EvidenceOwnershipError(f"VerificationResult '{verification.id}' run_id '{verification.run_id}' does not match ComplianceRun '{run.id}'.")

    @classmethod
    def normalize_fact_evidence(
        cls,
        db: Session,
        bidder_id: str,
        tender_id: str,
        fact: ExtractedFact,
        document: Document | None = None,
        run_id: str | None = None,
    ) -> Evidence:
        """Normalizes an ExtractedFact (document claim) into a canonical Evidence record."""
        cls.validate_ownership(bidder_id, tender_id, document=document, fact=fact)

        snippet_text = fact.source_text or f"Claimed value for {fact.field}: {fact.value}"
        doc_sha = document.sha256 if document else None
        doc_id = document.id if document else fact.document_id

        evidence = Evidence(
            id=str(uuid.uuid4()),
            entity_type="EXTRACTED_FACT",
            entity_id=fact.id,
            snippet=snippet_text,
            source_uri=None,  # Do not expose internal filesystem storage_uri
            page_number=fact.source_page,
            location_metadata={
                "field": fact.field,
                "confidence": fact.confidence,
                "document_filename": document.filename if document else None,
                "document_type": document.document_type.value if document and hasattr(document.document_type, "value") else str(getattr(document, "document_type", "")),
            },
            created_at=datetime.now(timezone.utc),
            bidder_id=bidder_id,
            tender_id=tender_id,
            document_id=doc_id,
            extracted_fact_id=fact.id,
            verification_result_id=None,
            run_id=run_id,
            source_type="DOCUMENT_CLAIM",
            source_reference=fact.field,
            sha256=doc_sha,
            verification_mode=VerificationMode.DOCUMENT,
            verification_status=VerificationStatus.UNVERIFIED,
            provider_identifier="DOCUMENT_EXTRACTION",
            observed_at=fact.created_at,
        )
        db.add(evidence)
        db.commit()
        db.refresh(evidence)
        return evidence

    @classmethod
    def normalize_verification_evidence(
        cls,
        db: Session,
        bidder_id: str,
        tender_id: str,
        verification: VerificationResult,
        run_id: str | None = None,
    ) -> Evidence:
        """Normalizes a VerificationResult into a canonical Evidence record preserving trust mode."""
        cls.validate_ownership(bidder_id, tender_id, verification=verification)

        mode_val = verification.mode
        source_val = verification.source.value if hasattr(verification.source, "value") else str(verification.source)
        snippet_text = f"Registry verification for {verification.field}: status={verification.status.value if hasattr(verification.status, 'value') else verification.status}, verified={verification.verified_value}"

        evidence = Evidence(
            id=str(uuid.uuid4()),
            entity_type="VERIFICATION_RESULT",
            entity_id=verification.id,
            snippet=snippet_text,
            source_uri=None,
            page_number=None,
            location_metadata={
                "field": verification.field,
                "claimed_value": verification.claimed_value,
                "verified_value": verification.verified_value,
                "verification_reference": verification.verification_reference,
            },
            created_at=datetime.now(timezone.utc),
            bidder_id=bidder_id,
            tender_id=tender_id,
            document_id=None,
            extracted_fact_id=None,
            verification_result_id=verification.id,
            run_id=run_id or verification.run_id,
            source_type="REGISTRY_VERIFICATION",
            source_reference=verification.verification_reference or verification.field,
            sha256=None,
            verification_mode=mode_val,
            verification_status=verification.status,
            provider_identifier=source_val,
            observed_at=verification.checked_at,
        )
        db.add(evidence)
        db.commit()
        db.refresh(evidence)
        return evidence
