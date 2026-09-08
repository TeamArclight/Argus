"""Offline, repeatable evaluation helpers; no network or production data required."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from .metrics import citation_precision, precision_recall, recall_at_k, structured_response_validity

DEFAULT_RELEASE_GATES = {
    "extraction_recall": 0.90,
    "retrieval_recall_at_5": 0.80,
    "citation_precision": 0.95,
    "risk_recall": 0.90,
    "unsupported_answer_rate": 0.05,
    "structured_validity": 0.99,
}

@dataclass(frozen=True)
class EvaluationReport:
    extraction_precision: float
    extraction_recall: float
    retrieval_recall_at_5: float
    retrieval_recall_at_10: float
    citation_precision: float
    risk_precision: float
    risk_recall: float
    unsupported_answer_rate: float
    structured_validity: float

def evaluate(*, expected_fields: set[str], extracted_fields: set[str], expected_evidence: set[str], ranked_evidence: Iterable[str], cited_evidence: Iterable[str], expected_risks: set[str], detected_risks: set[str], answer_count: int, unsupported_answers: int, structured_responses: Iterable[dict] = None, schema_type: type = None) -> EvaluationReport:
    extraction_precision, extraction_recall = precision_recall(extracted_fields, expected_fields)
    risk_precision, risk_recall = precision_recall(detected_risks, expected_risks)
    validity = 1.0
    if structured_responses is not None and schema_type is not None:
        validity = structured_response_validity(structured_responses, schema_type)
    return EvaluationReport(
        extraction_precision,
        extraction_recall,
        recall_at_k(expected_evidence, ranked_evidence, 5),
        recall_at_k(expected_evidence, ranked_evidence, 10),
        citation_precision(cited_evidence, expected_evidence),
        risk_precision,
        risk_recall,
        unsupported_answers / answer_count if answer_count else 0.0,
        validity
    )

def assert_release_gates(report: EvaluationReport, gates: dict[str, float] = None) -> None:
    """Raise a concise error when an evaluation report misses release thresholds."""
    thresholds = gates or DEFAULT_RELEASE_GATES
    failures = []
    for metric, threshold in thresholds.items():
        actual = getattr(report, metric)
        passed = actual <= threshold if metric == "unsupported_answer_rate" else actual >= threshold
        if not passed: failures.append(f"{metric}={actual:.3f} (threshold {threshold:.3f})")
    if failures: raise AssertionError("AI release gates failed: " + "; ".join(failures))
