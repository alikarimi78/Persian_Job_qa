from prisma import Prisma

from .config import settings

db = Prisma(datasource={"url": settings.DATABASE_URL}, auto_register=True)


def connect() -> None:
    if not db.is_connected():
        db.connect()


def disconnect() -> None:
    if db.is_connected():
        db.disconnect()


def get_db() -> Prisma:
    return db
