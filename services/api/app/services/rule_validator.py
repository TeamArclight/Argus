import math
from typing import Any
from fastapi import HTTPException, status
from app.compliance.engine import ComplianceEngine
from app.schemas.canonical import OperatorEnum, RequirementType


class RuleValidationError(ValueError):
    """Raised when an executable requirement rule fails validation."""
    pass


class RuleValidator:
    """Pure, deterministic validator for executable procurement requirements and candidate rules.
    
    Reuses ComplianceEngine normalization semantics without relying on ORM or database state.
    """

    @classmethod
    def validate(cls, rule_dict_or_obj: Any) -> dict[str, Any]:
        """Validates requirement properties. Accepts a dict, Pydantic model, or object.
        
        Returns a normalized dict of validated requirement fields or raises RuleValidationError.
        """
        if isinstance(rule_dict_or_obj, dict):
            data = rule_dict_or_obj
        elif hasattr(rule_dict_or_obj, "model_dump"):
            data = rule_dict_or_obj.model_dump()
        else:
            data = {
                "clause": getattr(rule_dict_or_obj, "clause", None),
                "requirement_type": getattr(rule_dict_or_obj, "requirement_type", None),
                "field": getattr(rule_dict_or_obj, "field", None),
                "operator": getattr(rule_dict_or_obj, "operator", None),
                "expected_value": getattr(rule_dict_or_obj, "expected_value", None),
                "unit": getattr(rule_dict_or_obj, "unit", None),
                "mandatory": getattr(rule_dict_or_obj, "mandatory", True),
                "confidence": getattr(rule_dict_or_obj, "confidence", 1.0),
                "source_page": getattr(rule_dict_or_obj, "source_page", None),
                "source_text": getattr(rule_dict_or_obj, "source_text", None),
            }

        # 1. Clause Validation
        clause = data.get("clause")
        if not clause or not isinstance(clause, str) or not clause.strip():
            raise RuleValidationError("Requirement clause must be a non-empty string.")

        # 2. Requirement Type Validation
        req_type = data.get("requirement_type")
        if isinstance(req_type, str):
            try:
                req_type = RequirementType(req_type)
            except ValueError:
                raise RuleValidationError(f"Unsupported requirement_type '{req_type}'.")
        elif not isinstance(req_type, RequirementType):
            raise RuleValidationError(f"Invalid requirement_type '{req_type}'.")

        # 3. Canonical Field Validation
        field = data.get("field")
        if not field or not isinstance(field, str) or not field.strip():
            raise RuleValidationError("Requirement field must be a non-empty canonical field key.")

        # 4. Operator Validation
        op = data.get("operator")
        if isinstance(op, str):
            try:
                op = OperatorEnum(op)
            except ValueError:
                raise RuleValidationError(f"Unsupported operator '{op}'.")
        elif not isinstance(op, OperatorEnum):
            raise RuleValidationError(f"Invalid operator '{op}'.")

        # 5. Expected Value & Operator/Value Semantics Validation
        expected_value = data.get("expected_value")

        # Check for NaN / Infinity
        if isinstance(expected_value, float) and not math.isfinite(expected_value):
            raise RuleValidationError("Requirement expected_value cannot be NaN or Infinity.")

        if op in (OperatorEnum.EXISTS, OperatorEnum.NOT_EXISTS):
            # No-threshold operators: expected_value can be boolean or True/False
            norm_bool = ComplianceEngine._normalize_bool(expected_value)
            if norm_bool is None and expected_value is not None:
                raise RuleValidationError(f"Operator '{op.value}' expects a boolean value, got '{expected_value}'.")
        elif op in (OperatorEnum.GTE, OperatorEnum.LTE, OperatorEnum.GT, OperatorEnum.LT, OperatorEnum.COUNT_GTE):
            # Numeric comparison operators require numeric threshold
            if expected_value is None:
                raise RuleValidationError(f"Operator '{op.value}' requires a non-null numeric threshold.")
            norm_num = ComplianceEngine._normalize_number(expected_value)
            if norm_num is None:
                raise RuleValidationError(f"Operator '{op.value}' expected_value '{expected_value}' cannot be normalized to a valid number.")
            if isinstance(norm_num, float) and not math.isfinite(norm_num):
                raise RuleValidationError("Requirement expected_value cannot be non-finite numeric value.")
        elif op in (OperatorEnum.DATE_BEFORE, OperatorEnum.DATE_AFTER):
            # Date operators require valid date string/datetime
            if expected_value is None:
                raise RuleValidationError(f"Operator '{op.value}' requires a non-null date expected_value.")
            norm_dt = ComplianceEngine._normalize_date(expected_value)
            if norm_dt is None:
                raise RuleValidationError(f"Operator '{op.value}' expected_value '{expected_value}' cannot be parsed as a valid ISO/standard date.")
        else:
            # General equality / inclusion operators (EQ, NE, IN, NOT_IN)
            if expected_value is None:
                raise RuleValidationError(f"Operator '{op.value}' requires a non-null expected_value.")

        # 6. Confidence Validation
        conf = data.get("confidence", 1.0)
        if conf is None:
            conf = 1.0
        try:
            conf = float(conf)
        except (ValueError, TypeError):
            raise RuleValidationError("Requirement confidence must be a valid float.")
        if not math.isfinite(conf) or conf < 0.0 or conf > 1.0:
            raise RuleValidationError("Requirement confidence must be a finite number between 0.0 and 1.0.")

        # 7. Source Page Validation
        source_page = data.get("source_page")
        if source_page is not None:
            if not isinstance(source_page, int) or source_page < 1:
                raise RuleValidationError("Requirement source_page must be an integer >= 1.")

        # 8. Source Text Validation
        source_text = data.get("source_text")
        if source_text is not None:
            if not isinstance(source_text, str):
                raise RuleValidationError("Requirement source_text must be a string.")
            if len(source_text) > 5000:
                raise RuleValidationError("Requirement source_text exceeds maximum length of 5000 characters.")

        # 9. Unit Validation
        unit = data.get("unit")
        if unit is not None and not isinstance(unit, str):
            raise RuleValidationError("Requirement unit must be a string.")

        return {
            "clause": clause.strip(),
            "requirement_type": req_type,
            "field": field.strip(),
            "operator": op,
            "expected_value": expected_value,
            "unit": unit.strip() if unit else None,
            "mandatory": bool(data.get("mandatory", True)),
            "confidence": conf,
            "source_page": source_page,
            "source_text": source_text,
        }

    @classmethod
    def validate_or_raise_http(cls, rule_dict_or_obj: Any) -> dict[str, Any]:
        """Validates rule and raises FastAPI HTTPException (422) if invalid."""
        try:
            return cls.validate(rule_dict_or_obj)
        except RuleValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(e),
            )
