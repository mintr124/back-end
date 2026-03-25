from sqlalchemy.orm import Session
from app.models.trace import Trace


class TraceRepository:
    def create(self, db: Session, trace: Trace) -> Trace:
        db.add(trace)
        db.flush()
        return trace

    def get_by_trace_id(self, db: Session, trace_id: str) -> Trace | None:
        return db.query(Trace).filter(Trace.trace_id == trace_id).one_or_none()
