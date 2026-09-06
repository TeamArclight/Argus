from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.domain import Evidence, ExtractedFact, RuleEvaluation, VerificationResult
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

        # Check ExtractedFact table
        fact_db = db.query(ExtractedFact).filter(ExtractedFact.id == ev_id).first()
        if fact_db:
            evidence_list.append(
                EvidenceRead(
                    id=fact_db.id,
                    entity_type="EXTRACTED_FACT",
                    entity_id=fact_db.document_id,
                    snippet=fact_db.source_text or f"Field '{fact_db.field}' value: {fact_db.value}",
                    source_uri=f"s3://bidders/{fact_db.bidder_id}/docs/{fact_db.document_id}",
                    page_number=fact_db.source_page,
                    location_metadata={"confidence": fact_db.confidence},
                    created_at=fact_db.created_at,
                )
            )
            continue

        # Check VerificationResult table
        ver_db = db.query(VerificationResult).filter(VerificationResult.id == ev_id).first()
        if ver_db:
            evidence_list.append(
                EvidenceRead(
                    id=ver_db.id,
                    entity_type="VERIFICATION_RESULT",
                    entity_id=ver_db.bidder_id,
                    snippet=f"Registry '{ver_db.source.value}' response for field '{ver_db.field}': status {ver_db.status.value}",
                    source_uri=ver_db.verification_reference,
                    location_metadata={"status": ver_db.status.value},
                    created_at=ver_db.checked_at,
                )
            )

    return evidence_list
