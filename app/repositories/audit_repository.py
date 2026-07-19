"""
Repositories for audit log and outbox event persistence.
"""
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.outbox_event import OutboxEvent


class AuditRepository:
    # Persist an audit log entry.
    def create_audit(self, db: Session, audit: AuditLog) -> AuditLog:
        db.add(audit)
        db.flush()
        return audit

    # Persist an outbox event entry.
    def create_event(self, db: Session, event: OutboxEvent) -> OutboxEvent:
        db.add(event)
        db.flush()
        return event

