"""
Service for persisting audit log entries and outbox events.
"""
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.outbox_event import OutboxEvent
from app.repositories.audit_repository import AuditRepository


class AuditService:
    def __init__(self):
        self.repo = AuditRepository()

    # Create and persist an audit log entry.
    def log_action(
        self,
        db: Session,
        *,
        trace_id: str,
        user_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        decision: str,
        input_json: dict | None = None,
        output_json: dict | None = None,
        job_id: str | None = None,
        latency_ms: int | None = None,
    ) -> AuditLog:
        rec = AuditLog(
            trace_id=trace_id,
            job_id=job_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            input_json=input_json,
            output_json=output_json,
            latency_ms=latency_ms,
        )
        self.repo.create_audit(db, rec)
        return rec

    # Create and persist an outbox event for downstream consumers.
    def emit_event(
        self,
        db: Session,
        *,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload_json: dict,
    ):
        ev = OutboxEvent(
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            payload_json=payload_json,
            status="queued",
            attempts=0,
        )
        self.repo.create_event(db, ev)
        return ev


# Module-level singleton; imported by services that require audit logging.
audit_service = AuditService()
