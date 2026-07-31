from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import create_token, get_current_user, verify_password
from ..database import get_db
from ..models import User
from ..schemas import LoginIn, MeOut, TokenOut

router = APIRouter(prefix="/auth", tags=["auth"])

# There is no public registration endpoint. Accounts are provisioned down the
# hierarchy — a unit_admin creates users, an org_admin creates unit admins, a
# super_admin creates organizations and their admins — under /accounts/*.


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    # Told apart from bad credentials on purpose: the password was right, and the person
    # needs to know to ask their admin rather than keep retrying it.
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is blocked")
    return TokenOut(access_token=create_token(user), role=user.role.value)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    """Who the caller is and where they sit. `organization` is resolved through the
    unit for a unit_admin or user, whose organization_id column is NULL by design."""
    organization = user.organization or (user.unit.organization if user.unit else None)
    return MeOut(id=user.id, username=user.username, role=user.role.value,
                 organization_id=user.organization_id, unit_id=user.unit_id,
                 organization=organization, unit=user.unit)
