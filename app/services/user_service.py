from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.user_oui_position import UserOuiPosition
from app.models.org_unit_instance import OrgUnitInstance
from app.models.org_unit import OrgUnit
from app.models.position import Position
from app.schemas.user import UserResponse, OuiPositionInfo, UserCreateRequest, UpdateUserRequest
from app.core.security import hash_password
from app.services.audit_service import audit_service

# OU name của root — dùng để xác định is_corp_member
CORP_OU_NAME = "Corp."


class UserService:

    # ── Helper: build UserResponse từ User ORM object ─────────────────────────

    def build_user_response(self, db: Session, user: User) -> UserResponse:
        """
        Build UserResponse đầy đủ với oui_positions.
        Tính max_clearance và is_corp_member từ tất cả positions.
        """
        positions_info: list[OuiPositionInfo] = []
        max_clearance = 1
        is_corp_member = False

        for uop in user.oui_positions:
            oui: OrgUnitInstance = uop.oui
            ou: OrgUnit = oui.ou
            pos: Position = uop.position

            info = OuiPositionInfo(
                oui_id=oui.id,
                oui_name=oui.name,
                ou_id=ou.id,
                ou_name=ou.name,
                position_id=pos.id,
                position_name=pos.name,
                clearance=pos.clearance,
                parent_oui_ids=[p.id for p in oui.parents],
            )
            positions_info.append(info)

            if pos.clearance > max_clearance:
                max_clearance = pos.clearance

            if ou.parent_id is None:  
                is_corp_member = True

        return UserResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            status=user.status,
            oui_positions=positions_info,
            max_clearance=max_clearance,
            is_corp_member=is_corp_member,
        )

    # ── List users ────────────────────────────────────────────────────────────

    def list_users(self, db: Session) -> list[UserResponse]:
        users = db.query(User).order_by(User.name).all()
        return [self.build_user_response(db, u) for u in users]

    # ── Create user ───────────────────────────────────────────────────────────

    def create_user(
        self,
        db: Session,
        actor: User,
        payload: UserCreateRequest,
        trace_id: str,
    ) -> User:
        # Chỉ Corp. member mới tạo được user
        actor_resp = self.build_user_response(db, actor)
        if not actor_resp.is_corp_member:
            raise HTTPException(status_code=403, detail="Corp-level admin required")

        existing = db.query(User).filter(User.email == payload.email).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Email '{payload.email}' already exists")

        user = User(
            email=payload.email,
            name=payload.name,
            status="active",
            password_hash=hash_password(payload.password),
        )
        db.add(user)
        db.flush()

        audit_service.log_action(
            db, trace_id=trace_id, user_id=actor.id,
            action="user.create", resource_type="user",
            resource_id=user.id, decision="allow",
            input_json={"email": payload.email, "name": payload.name},
        )
        db.commit()
        db.refresh(user)
        return user

    # ── Update user ───────────────────────────────────────────────────────────

    def update_user(
        self,
        db: Session,
        actor: User,
        user_id: str,
        payload: UpdateUserRequest,
    ) -> User:
        actor_resp = self.build_user_response(db, actor)
        if not actor_resp.is_corp_member:
            raise HTTPException(status_code=403, detail="Corp-level admin required")

        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if payload.status is not None:
            user.status = payload.status

        db.commit()
        db.refresh(user)
        return user

    # ── Get single user response ──────────────────────────────────────────────

    def get_user_response(self, db: Session, user_id: str) -> UserResponse:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return self.build_user_response(db, user)


user_service = UserService()