"""Database engine and session handling."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from netra.config import DB_URL


class Base(DeclarativeBase):
    pass


_connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from netra.core import models  # noqa: F401  (registers mappers)
    Base.metadata.create_all(engine)
