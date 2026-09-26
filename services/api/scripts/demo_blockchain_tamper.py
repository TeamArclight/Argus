"""ARGUS Blockchain Tamper Detection Demonstration Script.

Simulates unauthorized direct database modification of an anchored audit record
and proves that the cryptographic hash verification detects the tampering.

SAFETY RULES:
- Strictly fails closed if run against a production database.
- Bails out if APP_ENV == "production" or database host appears to be a cloud production instance.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path

# Ensure app package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.audit.canonical import hash_audit_event
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import AuditEvent
from app.services.blockchain_service import get_blockchain_service


PROD_HOST_PATTERNS = [
    "prod",
    "production",
    "amazonaws.com",
    "rds.",
    "cloudsql",
    "azure.com",
    "neon.tech",
    "supabase.co",
]


def assert_safe_environment(settings) -> None:
    """Refuse execution if running against any production environment."""
    env = (getattr(settings, "ENVIRONMENT", None) or os.getenv("APP_ENV", "")).lower()
    if env in ("production", "prod", "live"):
        sys.stderr.write("\n[FATAL] Tamper simulation is strictly prohibited in production environments.\n")
        sys.exit(1)

    db_url = (getattr(settings, "DATABASE_URL", None) or "").lower()
    for pattern in PROD_HOST_PATTERNS:
        if pattern in db_url and "local" not in db_url and "test" not in db_url:
            sys.stderr.write(
                f"\n[FATAL] Database URL appears to point to a production host ('{pattern}'). Execution aborted.\n"
            )
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Demonstrate cryptographic tamper detection for ARGUS audit events."
    )
    parser.add_argument(
        "--event-id",
        type=str,
        default=None,
        help="UUID of the audit event to inspect or tamper with. If omitted, picks the latest event.",
    )
    parser.add_argument(
        "--field",
        type=str,
        default="action",
        choices=["action", "message", "actor", "entity_id", "entity_type"],
        help="Field to modify (default: action)",
    )
    parser.add_argument(
        "--new-value",
        type=str,
        default="TAMPERED_BY_MALICIOUS_ACTOR",
        help="New value to inject into the database record (default: TAMPERED_BY_MALICIOUS_ACTOR)",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Run integrity verification without altering database data.",
    )
    parser.add_argument(
        "--restore",
        type=str,
        default=None,
        help="Restore the specified field to this original value.",
    )

    args = parser.parse_args()

    settings = get_settings()
    assert_safe_environment(settings)

    db = SessionLocal()
    blockchain_service = get_blockchain_service()

    try:
        # Locate target event
        if args.event_id:
            try:
                target_uuid = uuid.UUID(args.event_id)
            except ValueError:
                sys.stderr.write(f"\n[ERROR] Invalid UUID format: {args.event_id}\n")
                sys.exit(1)
            event = db.query(AuditEvent).filter(AuditEvent.id == str(target_uuid)).first()
            if not event:
                sys.stderr.write(f"\n[ERROR] AuditEvent with ID {args.event_id} not found.\n")
                sys.exit(1)
        else:
            # Pick latest event
            event = db.query(AuditEvent).order_by(AuditEvent.timestamp.desc()).first()
            if not event:
                sys.stderr.write("\n[ERROR] No audit events found in database. Create an event first.\n")
                sys.exit(1)

        print("\n" + "=" * 65)
        print(" ARGUS AUDIT TRAIL — CRYPTOGRAPHIC TAMPER VERIFICATION DEMO")
        print("=" * 65)
        print(f"Target Event ID:      {event.id}")
        print(f"Timestamp:            {event.timestamp}")
        print(f"Blockchain Status:    {event.blockchain_status}")
        print(f"Recorded Event Hash:  {event.event_hash or 'None'}")
        print(f"Current {args.field}:        {getattr(event, args.field)}")
        print("-" * 65)

        if args.restore is not None:
            print(f"\n[RESTORE] Restoring {args.field} to '{args.restore}'...")
            setattr(event, args.field, args.restore)
            db.commit()
            db.refresh(event)
            print(f"[RESTORE] Done. Field {args.field} is now '{getattr(event, args.field)}'.")

        if args.verify_only or args.restore is not None:
            print("\nRunning verification on current record...")
            recomputed = hash_audit_event(event)
            print(f"Recomputed Canonical Hash: {recomputed}")
            is_match = recomputed == event.event_hash
            print(f"Hash Matches Recorded:     {is_match}")

            res = blockchain_service.verify_event_integrity(db, event.id)
            print(f"Integrity Verdict:         {res.get('integrity')}")
            print(f"Details:                   {res.get('details')}")
            print("=" * 65 + "\n")
            return

        # Tamper simulation mode
        original_value = getattr(event, args.field)
        original_hash = event.event_hash or hash_audit_event(event)

        print(f"\n[STEP 1] Direct DB modification (bypassing application logger):")
        print(f"         Changing {args.field}: '{original_value}' -> '{args.new_value}'")
        setattr(event, args.field, args.new_value)
        db.commit()
        db.refresh(event)
        print("         Committed directly to PostgreSQL table 'audit_events'.")

        print(f"\n[STEP 2] Recomputing canonical hash v1 from current modified row:")
        tampered_hash = hash_audit_event(event)
        print(f"         Recorded Hash:   {original_hash}")
        print(f"         Recomputed Hash: {tampered_hash}")

        print(f"\n[STEP 3] Evaluating cryptographic integrity verdict:")
        integrity_res = blockchain_service.verify_event_integrity(db, event.id)
        verdict = integrity_res.get("integrity")

        print("-" * 65)
        if verdict == "TAMPERED":
            print(">>> VERDICT: [ALERT] TAMPERING DETECTED! <<<")
            print(f"Details: {integrity_res.get('details')}")
            print("\nSUCCESS: The cryptographic digest successfully proved that PostgreSQL data")
            print("was modified out-of-band after the immutable anchor was recorded.")
        elif verdict == "VERIFIED":
            print(">>> VERDICT: [OK] INTEGRITY VERIFIED (No tamper detected) <<<")
        else:
            print(f">>> VERDICT: {verdict} <<<")
            print(f"Details: {integrity_res.get('details')}")

        print("-" * 65)
        print(f"To restore original value run:")
        print(
            f"python scripts/demo_blockchain_tamper.py --event-id {event.id} --field {args.field} --restore \"{original_value}\""
        )
        print("=" * 65 + "\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
