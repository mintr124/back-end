from typing import Optional, List
from pydantic import BaseModel, ConfigDict


# ── OUI Position nested schema ────────────────────────────────────────────────

class OuiPositionInfo(BaseModel):
    """Thông tin position của user tại một OUI cụ thể."""
    oui_id: str
    oui_name: str
    ou_id: str
    ou_name: str
    position_id: str
    position_name: str
    clearance: int          # 1–5 (số nguyên, dễ so sánh)
    parent_oui_ids: list[str] = []

    model_config = ConfigDict(from_attributes=True)


# ── Request schemas ───────────────────────────────────────────────────────────

class UserCreateRequest(BaseModel):
    email: str
    name: str
    password: str
    # Không còn role / clearance_level / department_id
    # Assign OUI+Position qua POST /org-unit-instances/users/assign-oui


class UpdateUserRequest(BaseModel):
    status: Optional[str] = None
    # role/clearance không còn ở đây — thay đổi qua position


# ── Response schemas ──────────────────────────────────────────────────────────

class UserRead(BaseModel):
    """Shape tối thiểu — dùng trong JWT response."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    status: str


class UserResponse(BaseModel):
    """Full user response — dùng trong /users list."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    status: str
    oui_positions: List[OuiPositionInfo] = []

    # Computed helpers (populated in service)
    max_clearance: int = 1          # max clearance từ tất cả positions
    is_corp_member: bool = False    # có position tại Corp. OUI không