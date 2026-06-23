from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.system_setting import SystemSetting

# Default values used when a key has never been saved
DEFAULTS: dict[str, Any] = {
    "rag.top_k": 5,
    "rag.similarity_threshold": 0.0,
    "rag.hybrid_search": True,
}


class SystemSettingRepository:

    def get(self, db: Session, key: str) -> Any:
        row = db.get(SystemSetting, key)
        if row is None:
            return DEFAULTS.get(key)
        try:
            return json.loads(row.value)
        except (ValueError, TypeError):
            return row.value

    def get_all(self, db: Session) -> dict[str, Any]:
        rows = db.query(SystemSetting).all()
        result = dict(DEFAULTS)
        for row in rows:
            try:
                result[row.key] = json.loads(row.value)
            except (ValueError, TypeError):
                result[row.key] = row.value
        return result

    def set(self, db: Session, key: str, value: Any) -> None:
        serialized = json.dumps(value)
        row = db.get(SystemSetting, key)
        if row is None:
            db.add(SystemSetting(key=key, value=serialized, updated_at=datetime.utcnow()))
        else:
            row.value = serialized
            row.updated_at = datetime.utcnow()

    def set_many(self, db: Session, data: dict[str, Any]) -> None:
        for key, value in data.items():
            self.set(db, key, value)
        db.commit()


system_setting_repository = SystemSettingRepository()
