from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import bcrypt
from prisma import Prisma

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
        # `user.role` is already the plain string: Prisma's models are configured with
        # `use_enum_values`, so a role field holds 'super_admin' rather than
        # `Role.super_admin`. Comparisons against `Role.…` still work — the generated
        # enum is a `StrEnum` — but there is no `.value` to reach for any more.
        "role": user.role,
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
    db: Prisma = Depends(get_db),
) -> User | None:
    payload = _decode(credentials)
    if payload is None:
        return None
    # Looked up rather than trusted from the token: the role, the organization scope and
    # the blocked flag are all authorization input, and a token minted before a change
    # must not keep old rights.
    #
    # Nothing is included: the caller's scope is a column on this row. `organization`
    # deliberately is not — including it would pull the logo blob into every
    # authenticated request, so the two endpoints that need the organization fetch it
    # narrowed (`accounts.organization_of`).
    user = db.user.find_unique(where={"id": int(payload["sub"])})
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
    organization they may touch) is a separate check, done in the handler against the
    target record — see `accounts.assert_can_manage_*`."""
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
