from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..accounts import set_name, set_password
from ..auth import create_token, get_current_user, verify_password
from ..database import get_db
from ..models import User
from ..rate_limit import login_key, login_limiter
from ..schemas import LoginIn, MeOut, NameIn, SelfPasswordIn, TokenOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# There is no public registration endpoint. Accounts are provisioned down the
# hierarchy — a unit_admin creates users, an org_admin creates unit admins, a
# super_admin creates organizations and their admins — under /accounts/*.


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    """Rate limited per source *and* username (`app/rate_limit.py`), and only wrong
    passwords spend from that budget: guessing one account runs out of attempts, while
    someone who signs in correctly all day never does. The check comes first so a
    refused caller does not get the bcrypt comparison done for them either."""
    key = login_key(request, body.username)
    login_limiter.check(key)

    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.hashed_password):
        # Charged for a username that does not exist too — otherwise the 401 that costs
        # nothing tells an attacker which names are worth guessing at.
        login_limiter.hit(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")
    # Told apart from bad credentials on purpose: the password was right, and the person
    # needs to know to ask their admin rather than keep retrying it.
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is blocked")
    login_limiter.reset(key)
    return TokenOut(access_token=create_token(user), role=user.role.value)


@router.post("/password", response_model=UserOut)
def change_own_password(body: SelfPasswordIn, request: Request,
                        user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    """The caller's own password — the one password endpoint that acts on the caller.

    `/accounts/{id}/password` deliberately cannot: nobody may act on their own account
    there, which is right for blocking and deletion but leaves a super_admin unable to
    change their own password at all, since there is nobody above them to do it for
    them. This is what fills that gap, at every level rather than only at the top.

    What stands in for an admin's authority here is the current password, and it is not
    a formality: without it a token left behind on a shared machine — good for an hour —
    would be enough to take the account for good.

    Wrong attempts are charged to the same budget as login, on the same source+username
    key: both are password guesses against one account, and getting it right gives the
    budget back either way.
    """
    key = login_key(request, user.username)
    login_limiter.check(key)
    if not verify_password(body.current_password, user.hashed_password):
        login_limiter.hit(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect")
    login_limiter.reset(key)
    return set_password(db, user, body.new_password)


@router.post("/name", response_model=UserOut)
def change_own_name(body: NameIn, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """The caller's own name. Deliberately does *not* ask for a password, unlike
    `/auth/password`: a display name is not a credential — it cannot be used to sign in,
    to reach anything, or to take an account over — so the token the caller already
    holds is proof enough.

    It exists for the same reason the self-service password endpoint does: an admin can
    fix the name on any account below them, and a super_admin has nobody above them. The
    first super_admin in particular is created by the seed script from two environment
    variables that carry no name at all, so without this the top of the hierarchy would
    be the one account whose reports could never be headed by a person.
    """
    return set_name(db, user, body.first_name, body.last_name)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    """Who the caller is and where they sit. `organization` is resolved through the
    unit for a unit_admin or user, whose organization_id column is NULL by design."""
    organization = user.organization or (user.unit.organization if user.unit else None)
    return MeOut(id=user.id, username=user.username, role=user.role.value,
                 first_name=user.first_name, last_name=user.last_name,
                 full_name=user.full_name,
                 organization_id=user.organization_id, unit_id=user.unit_id,
                 organization=organization, unit=user.unit)
