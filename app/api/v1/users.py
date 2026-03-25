from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.models.user import User
from app.schemas.user import UserRead

router = APIRouter()

@router.get("/users", response_model=list[UserRead])
def get_all_users(db: Session = Depends(get_db)):
    return db.query(User).all()