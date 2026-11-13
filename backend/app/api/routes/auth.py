"""Sign-up, login, logout and profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import bearer_token, current_user
from app.database.connection import get_db
from app.database.models import User
from app.schemas.auth import LoginRequest, LoginResponse, PasswordChange, ProfileUpdate, SignupRequest, UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=UserOut, status_code=201)
def signup(body: SignupRequest, db: Session = Depends(get_db)) -> User:
    return auth_service.signup(db, body.email, body.full_name, body.password)


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user, token, expires = auth_service.login(db, body.email, body.password, body.remember)
    return LoginResponse(token=token, expires_at=expires, user=UserOut.model_validate(user))


@router.post("/logout")
def logout(token: str | None = Depends(bearer_token), db: Session = Depends(get_db)) -> dict[str, bool]:
    if token:
        auth_service.logout(db, token)
    return {"logged_out": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfileUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(user, key, value.strip() if isinstance(value, str) and key == "full_name" else value)
    db.commit()
    db.refresh(user)
    return user


@router.post("/change-password")
def change_password(body: PasswordChange, user: User = Depends(current_user),
                    db: Session = Depends(get_db)) -> dict[str, bool]:
    auth_service.change_password(db, user, body.current_password, body.new_password)
    return {"changed": True}
