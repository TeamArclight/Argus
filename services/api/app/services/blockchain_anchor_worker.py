import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, settings
from app.db.session import SessionLocal
from app.models.domain import AuditEvent
from app.services.blockchain_service import BlockchainAuditService

logger = logging.getLogger("argus.blockchain_worker")

# Bounded exponential backoff intervals in seconds: 30s, 60s, 120s, 240s, 480s
RETRY_BACKOFF_INTERVALS = [30, 60, 120, 240, 480]


def get_next_retry_interval(retry_count: int) -> int:
    """Calculates bounded exponential backoff delay based on retry count."""
    if retry_count < 0:
        return RETRY_BACKOFF_INTERVALS[0]
    idx = min(retry_count, len(RETRY_BACKOFF_INTERVALS) - 1)
    return RETRY_BACKOFF_INTERVALS[idx]


class BlockchainAnchorWorker:
    """Background processor for anchoring pending and retryable audit events to blockchain."""

    def __init__(self, cfg: Settings | None = None, service: BlockchainAuditService | None = None):
        self.cfg = cfg or settings
        self.service = service or BlockchainAuditService(self.cfg)

    def process_event_by_id(self, event_id: str) -> bool:
        """Processes a single AuditEvent by ID in an isolated database transaction.
        
        Returns True if successfully confirmed, False otherwise.
        """
        if not self.service.is_enabled():
            return False

        db = SessionLocal()
        try:
            event = db.query(AuditEvent).filter(AuditEvent.id == event_id).first()
            if not event:
                return False

            # If already confirmed, nothing to do
            if event.blockchain_status == "CONFIRMED":
                return True

            return self._anchor_and_update(db, event)
        finally:
            db.close()

    def process_batch(self, limit: int = 10) -> int:
        """Polls and processes a batch of eligible PENDING, retryable FAILED, or recovering SUBMITTED events.
        
        Returns count of events successfully processed.
        """
        if not self.service.is_enabled():
            return 0

        db = SessionLocal()
        now_utc = datetime.now(timezone.utc)
        confirmed_count = 0

        try:
            bind = db.get_bind()
            query = db.query(AuditEvent)

            # Match PENDING, recovering SUBMITTED, or retryable FAILED
            query = query.filter(
                (AuditEvent.blockchain_status == "PENDING")
                | (
                    (AuditEvent.blockchain_status == "FAILED")
                    & (AuditEvent.blockchain_retry_count < self.cfg.BLOCKCHAIN_MAX_RETRIES)
                    & (
                        (AuditEvent.blockchain_next_retry_at == None)  # noqa: E711
                        | (AuditEvent.blockchain_next_retry_at <= now_utc)
                    )
                )
                | (
                    (AuditEvent.blockchain_status == "SUBMITTED")
                    & (AuditEvent.blockchain_last_attempt_at <= now_utc - timedelta(seconds=60))
                )
            ).order_by(AuditEvent.timestamp.asc())

            # PostgreSQL row-level locking
            if bind is not None and getattr(getattr(bind, "dialect", None), "name", None) == "postgresql":
                query = query.with_for_update(skip_locked=True)

            candidates = query.limit(limit).all()
            for event in candidates:
                try:
                    success = self._anchor_and_update(db, event)
                    if success:
                        confirmed_count += 1
                except Exception as exc:
                    logger.error("Error processing anchor for event %s: %s", event.id, exc)

            return confirmed_count
        finally:
            db.close()

    def _anchor_and_update(self, db: Session, event: AuditEvent) -> bool:
        """Anchors an event, recording status transitions and handling errors safely."""
        now_utc = datetime.now(timezone.utc)
        event_hash = event.event_hash
        if not event_hash:
            event_hash = self.service.compute_event_hash(event)
            event.event_hash = event_hash

        event.blockchain_last_attempt_at = now_utc
        event.blockchain_status = "SUBMITTED"
        event.blockchain_network = "polygon-amoy"
        db.commit()

        try:
            result = self.service.submit_anchor(event.id, event_hash)
            event.blockchain_status = "CONFIRMED"
            event.blockchain_tx_hash = result.get("transaction_hash")
            event.blockchain_block_number = result.get("block_number")
            event.anchored_at = result.get("anchored_at") or now_utc
            event.blockchain_error = None
            db.commit()
            logger.info("Successfully anchored AuditEvent %s on-chain (tx: %s)", event.id, event.blockchain_tx_hash)
            return True
        except Exception as exc:
            event.blockchain_retry_count = (event.blockchain_retry_count or 0) + 1
            event.blockchain_status = "FAILED"
            event.blockchain_error = str(exc)

            # Schedule next retry with bounded exponential backoff
            backoff_secs = get_next_retry_interval(event.blockchain_retry_count)
            event.blockchain_next_retry_at = now_utc + timedelta(seconds=backoff_secs)
            db.commit()
            logger.warning(
                "Failed to anchor AuditEvent %s (attempt %d/%d, next retry in %ds): %s",
                event.id,
                event.blockchain_retry_count,
                self.cfg.BLOCKCHAIN_MAX_RETRIES,
                backoff_secs,
                exc,
            )
            return False
