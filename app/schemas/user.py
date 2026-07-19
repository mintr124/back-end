from typing import Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict


# OUI Position nested schema.
class OuiPositionInfo(BaseModel):
    # The user's position information within a specific OUI.
    oui_id: str
    oui_name: str
    ou_id: str
    ou_name: str
    position_id: str
    position_name: str
    clearance: int          # 1–5 (integers, easy to compare).
    parent_oui_ids: list[str] = []

    model_config = ConfigDict(from_attributes=True)


# Request schemas
class UserCreateRequest(BaseModel):
    email: str
    name: str
    password: str
    # Assign OUI+Position through POST /org-unit-instances/users/assign-oui.


class UpdateUserRequest(BaseModel):
    status: Optional[str] = None


# ── Response schemas ──────────────────────────────────────────────────────────

class UserRead(BaseModel):
    # Minimal shape — used in JWT responses.
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    status: str


class UserResponse(BaseModel):
    # Full user response — use in /users list.
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    status: str
    created_at: Optional[datetime] = None
    oui_positions: list[OuiPositionInfo] = []

    # Computed helpers (populated in service).
    max_clearance: int = 1          # max clearance from all positions.
    is_corp_member: bool = False    # has position at Corp. OUI or not.