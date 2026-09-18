"""Canonical Field Registry for ARGUS Platform.

Centralizes all standard procurement eligibility criteria fields, their aliases,
categories, allowed operators, data types, and unit semantics.

Ensures seamless interoperability across:
- Requirement extraction (tender analysis)
- Fact extraction (bidder document analysis)
- External verification adapters (GST, EPFO, MCA, Udyam)
- Compliance rule evaluation (ComplianceEngine)
- Risk and anomaly engine
- Frontend form selectors and badges
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from app.schemas.canonical import OperatorEnum, RequirementType


@dataclass(frozen=True)
class CanonicalFieldDefinition:
    key: str
    category: str
    display_name: str
    data_type: str  # NUMBER, CURRENCY, BOOLEAN, STRING, DATE, ENUM
    requirement_type: RequirementType
    allowed_operators: tuple[OperatorEnum, ...]
    unit_semantics: str | None
    aliases: tuple[str, ...]
    description: str = ""


CANONICAL_FIELDS: tuple[CanonicalFieldDefinition, ...] = (
    CanonicalFieldDefinition(
        key="financial.average_annual_turnover",
        category="Financial",
        display_name="Average Annual Turnover",
        data_type="CURRENCY",
        requirement_type=RequirementType.TURNOVER,
        allowed_operators=(
            OperatorEnum.GTE,
            OperatorEnum.GT,
            OperatorEnum.LTE,
            OperatorEnum.LT,
            OperatorEnum.EQ,
            OperatorEnum.EXISTS,
        ),
        unit_semantics="INR",
        aliases=(
            "turnover",
            "annual_turnover",
            "avg_annual_turnover",
            "average_annual_turnover",
            "financial.annual_turnover",
            "financial.turnover",
            "financial.avg_turnover",
            "annual_turnover_inr",
            "average_turnover",
            "financial.average_turnover",
        ),
        description="Average annual financial turnover over the designated past financial years.",
    ),
    CanonicalFieldDefinition(
        key="tax.gstin",
        category="Tax",
        display_name="GSTIN (Goods and Services Tax ID)",
        data_type="STRING",
        requirement_type=RequirementType.GST,
        allowed_operators=(
            OperatorEnum.EXISTS,
            OperatorEnum.EQ,
            OperatorEnum.NE,
        ),
        unit_semantics=None,
        aliases=(
            "gst",
            "gstin",
            "tax.gst",
            "tax.gstin",
            "gst_number",
            "gst_registration",
            "general.gstin",
        ),
        description="15-character Goods and Services Tax Identification Number.",
    ),
    CanonicalFieldDefinition(
        key="gem.seller_id",
        category="Procurement & Platform",
        display_name="GeM Seller Registration / ID",
        data_type="STRING",
        requirement_type=RequirementType.CUSTOM,
        allowed_operators=(
            OperatorEnum.EXISTS,
            OperatorEnum.EQ,
            OperatorEnum.NE,
        ),
        unit_semantics=None,
        aliases=(
            "gem",
            "gem_id",
            "gem_seller_id",
            "gem.seller_id",
            "registration.gem",
            "procurement.gem_registration",
            "gem_registration",
            "gem_portal_registration",
        ),
        description="Government e-Marketplace (GeM) seller registration ID or portal registration confirmation.",
    ),
    CanonicalFieldDefinition(
        key="identity.pan",
        category="Corporate",
        display_name="Permanent Account Number (PAN)",
        data_type="STRING",
        requirement_type=RequirementType.CUSTOM,
        allowed_operators=(
            OperatorEnum.EXISTS,
            OperatorEnum.EQ,
            OperatorEnum.NE,
        ),
        unit_semantics=None,
        aliases=(
            "pan",
            "pan_number",
            "tax.pan",
            "corporate.pan",
            "general.pan",
            "identity.pan",
        ),
        description="10-character Permanent Account Number issued by Income Tax Department.",
    ),
    CanonicalFieldDefinition(
        key="registration.udyam",
        category="MSME",
        display_name="Udyam Registration Number",
        data_type="STRING",
        requirement_type=RequirementType.UDYAM,
        allowed_operators=(
            OperatorEnum.EXISTS,
            OperatorEnum.EQ,
            OperatorEnum.NE,
        ),
        unit_semantics=None,
        aliases=(
            "udyam",
            "udyam_registration",
            "msme_number",
            "udyam_number",
            "msme.udyam",
            "msme.udyam_registration",
            "registration.udyam",
            "msme.udyam_number",
        ),
        description="MSME Udyam Registration Certificate number.",
    ),
    CanonicalFieldDefinition(
        key="corporate.cin",
        category="Corporate",
        display_name="Corporate Identification Number (CIN)",
        data_type="STRING",
        requirement_type=RequirementType.MCA,
        allowed_operators=(
            OperatorEnum.EXISTS,
            OperatorEnum.EQ,
            OperatorEnum.NE,
        ),
        unit_semantics=None,
        aliases=(
            "cin",
            "company_cin",
            "corporate.cin",
            "cin_number",
            "mca.cin",
            "corporate.mca_registration",
            "general.cin",
            "mca",
            "mca_registration",
        ),
        description="21-digit Corporate Identification Number registered with MCA.",
    ),
    CanonicalFieldDefinition(
        key="labour.epfo_registration",
        category="Labour & Statutory",
        display_name="EPFO Registration Number",
        data_type="STRING",
        requirement_type=RequirementType.EPFO,
        allowed_operators=(
            OperatorEnum.EXISTS,
            OperatorEnum.EQ,
            OperatorEnum.NE,
        ),
        unit_semantics=None,
        aliases=(
            "epfo",
            "epf",
            "epfo_number",
            "epf_registration",
            "statutory.epf",
            "labour.epfo_registration",
            "statutory.epfo",
            "general.epfo",
            "epfo_registration",
        ),
        description="Employees' Provident Fund Organisation establishment code.",
    ),
    CanonicalFieldDefinition(
        key="labour.esic_registration",
        category="Labour & Statutory",
        display_name="ESIC Registration Number",
        data_type="STRING",
        requirement_type=RequirementType.ESIC,
        allowed_operators=(
            OperatorEnum.EXISTS,
            OperatorEnum.EQ,
            OperatorEnum.NE,
        ),
        unit_semantics=None,
        aliases=(
            "esic",
            "esi",
            "esic_number",
            "esi_registration",
            "statutory.esi",
            "labour.esic_registration",
            "statutory.esic",
            "general.esic",
            "esic_registration",
        ),
        description="Employees' State Insurance Corporation 17-digit code.",
    ),
    CanonicalFieldDefinition(
        key="experience.years",
        category="Experience",
        display_name="Past Experience (Years)",
        data_type="NUMBER",
        requirement_type=RequirementType.EXPERIENCE,
        allowed_operators=(
            OperatorEnum.GTE,
            OperatorEnum.GT,
            OperatorEnum.LTE,
            OperatorEnum.LT,
            OperatorEnum.EQ,
            OperatorEnum.EXISTS,
        ),
        unit_semantics="years",
        aliases=(
            "experience_years",
            "years_in_business",
            "past_experience_years",
            "experience.years",
            "experience.years_in_business",
            "experience",
        ),
        description="Number of completed years of experience in the relevant sector.",
    ),
    CanonicalFieldDefinition(
        key="legal.blacklisted",
        category="Legal & Compliance",
        display_name="Debarment / Blacklist Status",
        data_type="BOOLEAN",
        requirement_type=RequirementType.BLACK_LIST,
        allowed_operators=(
            OperatorEnum.EQ,
            OperatorEnum.NE,
            OperatorEnum.EXISTS,
        ),
        unit_semantics=None,
        aliases=(
            "blacklisted",
            "debarred",
            "blacklist_status",
            "debarment_status",
            "debarment.status",
            "legal.blacklisted",
            "debarment.blacklisted",
        ),
        description="Boolean indicating whether the entity is debarred or blacklisted by any government authority.",
    ),
    CanonicalFieldDefinition(
        key="document.expiry_date",
        category="Document",
        display_name="Document Expiry Date",
        data_type="DATE",
        requirement_type=RequirementType.CUSTOM,
        allowed_operators=(
            OperatorEnum.GTE,
            OperatorEnum.GT,
            OperatorEnum.LTE,
            OperatorEnum.LT,
            OperatorEnum.EQ,
            OperatorEnum.EXISTS,
        ),
        unit_semantics=None,
        aliases=(
            "expiry_date",
            "valid_upto",
            "valid_until",
            "document.expiry_date",
            "document.valid_upto",
        ),
        description="Validity or expiry date of the submitted certificate/document.",
    ),
)


# Build fast lookup indexes
CANONICAL_FIELD_MAP: dict[str, CanonicalFieldDefinition] = {
    field.key: field for field in CANONICAL_FIELDS
}

ALIAS_TO_CANONICAL_MAP: dict[str, str] = {}
for f in CANONICAL_FIELDS:
    ALIAS_TO_CANONICAL_MAP[f.key.lower().strip()] = f.key
    for alias in f.aliases:
        ALIAS_TO_CANONICAL_MAP[alias.lower().strip()] = f.key


def resolve_canonical_field(field_name: str | None) -> str:
    """Resolves an arbitrary or alias field name to its canonical key.

    Returns the canonical key if known; otherwise returns the stripped original field name.
    Normalizes spaces and underscores for flexible phrase matching.
    """
    if not field_name:
        return ""
    normalized = field_name.strip().lower()
    return (
        ALIAS_TO_CANONICAL_MAP.get(normalized)
        or ALIAS_TO_CANONICAL_MAP.get(normalized.replace(" ", "_"))
        or ALIAS_TO_CANONICAL_MAP.get(normalized.replace("_", " "))
        or field_name.strip()
    )


def get_canonical_field_definition(key_or_alias: str | None) -> CanonicalFieldDefinition | None:
    """Returns the CanonicalFieldDefinition for a given key or alias, or None if unknown."""
    canonical_key = resolve_canonical_field(key_or_alias)
    return CANONICAL_FIELD_MAP.get(canonical_key)


def are_fields_equivalent(field_a: str | None, field_b: str | None) -> bool:
    """Checks whether two field names resolve to the same canonical field."""
    if not field_a or not field_b:
        return False
    return resolve_canonical_field(field_a) == resolve_canonical_field(field_b)
