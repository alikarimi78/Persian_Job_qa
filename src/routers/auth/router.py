from fastapi import APIRouter, Depends, HTTPException, Request, status
from prisma import Prisma

from src.database import get_db
from src.models import User
from src.rate_limit import login_key, login_limiter
from src.security import create_token, get_current_user, verify_password
from src.routers.accounts.schemas import NameIn, UserOut
from src.routers.accounts.service import organization_of, set_name, set_password

from .schemas import LoginIn, MeOut, SelfPasswordIn, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Prisma = Depends(get_db)):
    key = login_key(request, body.username)
    login_limiter.check(key)

    user = db.user.find_unique(where={"username": body.username})
    if not user or not verify_password(body.password, user.hashed_password):
        login_limiter.hit(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is blocked")
    login_limiter.reset(key)
    return TokenOut(access_token=create_token(user), role=user.role)


@router.post("/password", response_model=UserOut)
def change_own_password(body: SelfPasswordIn, request: Request,
                        user: User = Depends(get_current_user),
                        db: Prisma = Depends(get_db)):
    key = login_key(request, user.username)
    login_limiter.check(key)
    if not verify_password(body.current_password, user.hashed_password):
        login_limiter.hit(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")
    login_limiter.reset(key)
    return set_password(db, user, body.new_password)


@router.post("/name", response_model=UserOut)
def change_own_name(body: NameIn, user: User = Depends(get_current_user),
                    db: Prisma = Depends(get_db)):
    return set_name(db, user, body.first_name, body.last_name)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Prisma = Depends(get_db)):
    return MeOut(id=user.id, username=user.username, role=user.role,
                 first_name=user.first_name, last_name=user.last_name,
                 organization_id=user.organization_id,
                 organization=organization_of(db, user))
