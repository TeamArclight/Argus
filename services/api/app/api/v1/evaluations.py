from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.domain import Document, Evidence, ExtractedFact, RuleEvaluation, VerificationResult
from app.schemas.canonical import EvidenceRead

router = APIRouter(prefix="/evaluations", tags=["Evaluations & Evidence"])


@router.get("/{id}/evidence", response_model=list[EvidenceRead])
def get_evaluation_evidence(id: str, db: Session = Depends(get_db)):
    rule_eval = db.query(RuleEvaluation).filter(RuleEvaluation.id == id).first()
    if not rule_eval:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rule evaluation with ID {id} not found.",
        )

    evidence_list: list[EvidenceRead] = []

    # Reconstruct evidence traces from saved evidence_ids (facts, verification results, or explicit evidence entries)
    for ev_id in rule_eval.evidence_ids:
        # Check explicit Evidence table
        evi_db = db.query(Evidence).filter(Evidence.id == ev_id).first()
        if evi_db:
            evidence_list.append(EvidenceRead.model_validate(evi_db))
            continue

        # Check ExtractedFact table and retrieve real Document storage URI
        fact_db = db.query(ExtractedFact).filter(ExtractedFact.id == ev_id).first()
        if fact_db:
            doc_db = db.query(Document).filter(Document.id == fact_db.document_id).first()
            real_source_uri = doc_db.storage_uri if doc_db else None

            evidence_list.append(
                EvidenceRead(
                    id=fact_db.id,
                    entity_type="EXTRACTED_FACT",
                    entity_id=fact_db.document_id,
                    snippet=fact_db.source_text or f"Field '{fact_db.field}' value: {fact_db.value}",
                    source_uri=real_source_uri,
                    page_number=fact_db.source_page,
                    location_metadata={"confidence": fact_db.confidence, "filename": doc_db.filename if doc_db else None},
                    created_at=fact_db.created_at,
                )
            )
            continue

        # Check VerificationResult table
        ver_db = db.query(VerificationResult).filter(VerificationResult.id == ev_id).first()
        if ver_db:
            source_val = ver_db.source.value if hasattr(ver_db.source, "value") else str(ver_db.source)
            status_val = ver_db.status.value if hasattr(ver_db.status, "value") else str(ver_db.status)
            evidence_list.append(
                EvidenceRead(
                    id=ver_db.id,
                    entity_type="VERIFICATION_RESULT",
                    entity_id=ver_db.bidder_id,
                    snippet=f"Registry '{source_val}' response for field '{ver_db.field}': status {status_val}",
                    source_uri=ver_db.verification_reference,
                    location_metadata={"status": status_val},
                    created_at=ver_db.checked_at,
                )
            )

    return evidence_list
