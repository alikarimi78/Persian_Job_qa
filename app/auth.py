from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import bcrypt
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import Role, User

bearer = HTTPBearer(auto_error=False)


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(p: str, hashed: str) -> bool:
    return bcrypt.checkpw(p.encode("utf-8"), hashed.encode("utf-8"))


def create_token(user: User) -> str:
    payload = {
        "sub": str(user.id),
        "role": user.role.value,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def _decode(credentials: HTTPAuthorizationCredentials | None) -> dict | None:
    if credentials is None:
        return None
    try:
        return jwt.decode(credentials.credentials, settings.JWT_SECRET,
                          algorithms=[settings.JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")


def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User | None:
    payload = _decode(credentials)
    if payload is None:
        return None
    # Looked up rather than trusted from the token: the role, the org/unit scope and the
    # blocked flag are all authorization input, and a token minted before a change must
    # not keep old rights.
    user = db.get(User, int(payload["sub"]))
    if user is not None and not user.is_active:
        # Checked here rather than only at login, so blocking takes effect at once
        # instead of when the token happens to expire.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is blocked")
    return user


def get_current_user(user: User | None = Depends(get_current_user_optional)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return user


def require_roles(*roles: Role):
    """Dependency factory gating an endpoint on the caller's role. Scope (*which*
    organization or unit they may touch) is a separate check, done in the handler
    against the target record — see `accounts.assert_can_manage_*`."""
    allowed = set(roles)

    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return user

    return dependency


# The old single `admin` role became super_admin, so the job-moderation endpoints it
# guarded are super-admin-only: approving a suggestion writes to the one global corpus
# every organization searches, which is not an organization-level decision.
require_super_admin = require_roles(Role.super_admin)
