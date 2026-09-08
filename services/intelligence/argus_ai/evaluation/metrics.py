from __future__ import annotations
from typing import Iterable

def recall_at_k(expected_ids: set[str], ranked_ids: Iterable[str], k: int) -> float:
    return len(expected_ids & set(list(ranked_ids)[:k])) / len(expected_ids) if expected_ids else 1.0

def citation_precision(cited_ids: Iterable[str], supported_ids: set[str]) -> float:
    cited = list(cited_ids); return sum(item in supported_ids for item in cited) / len(cited) if cited else 0.0

def precision_recall(predicted: set[str], expected: set[str]) -> tuple[float, float]:
    return (len(predicted & expected) / len(predicted) if predicted else 0.0, len(predicted & expected) / len(expected) if expected else 1.0)

def structured_response_validity(responses: Iterable[dict], schema_type: type) -> float:
    """Fraction of responses that validate against the given Pydantic model."""
    valid_count = 0
    total = 0
    for resp in responses:
        total += 1
        try:
            schema_type.model_validate(resp)
            valid_count += 1
        except Exception:
            pass
    return valid_count / total if total > 0 else 1.0
