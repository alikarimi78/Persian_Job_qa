"""The Prisma client, as one connected singleton for the whole process.

This replaces SQLAlchemy's engine + `sessionmaker` + per-request `Session`, and the
shapes are not equivalent: a `Session` was a unit of work that had to be created and
closed around every request, while a `Prisma` client is a connection pool that is opened
once and shared. `get_db` therefore hands the *same* object to every request and closes
nothing — the `try/finally` the old dependency needed has no counterpart here.

Two consequences worth knowing:

- **There are no transactions unless you ask for one.** Every call is committed on its
  own, so the `db.commit()` that used to end each handler is gone rather than implicit.
  Where two writes have to land together, `db.tx()` is the replacement; nothing in this
  app currently needs one (`accounts.py` writes a single row per operation), which is why
  none appears.
- **Nothing is lazy-loaded.** SQLAlchemy filled `user.unit` on first access; Prisma
  leaves a relation `None` unless the query said `include={"unit": True}`. Anything
  reading through a relation has to load it — `accounts.get_account` and
  `auth.get_current_user` are where that is done, and the reason both name `unit`
  explicitly.

The client is constructed with the URL from `settings` rather than left to read
`DATABASE_URL` itself, so the five-part assembly in `config.py` is what decides it in
every case. `auto_register=True` puts this client in Prisma's registry, which is what
lets the narrowed models in `prisma.partials` (`OrganizationSummary`) be used without
being handed one.
"""

from prisma import Prisma

from .config import settings

db = Prisma(datasource={"url": settings.DATABASE_URL}, auto_register=True)


def connect() -> None:
    """Opens the connection pool. Called once, from `main.py`'s lifespan."""
    if not db.is_connected():
        db.connect()


def disconnect() -> None:
    if db.is_connected():
        db.disconnect()


def get_db() -> Prisma:
    """The FastAPI dependency. Returns the shared client; `tests/conftest.py` overrides
    it with a client pointed at the test database."""
    return db
