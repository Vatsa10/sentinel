"""Database engine and session handling."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from netra.config import DB_URL


class Base(DeclarativeBase):
    pass


_connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=_connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


#: Columns added to tables that already exist in the field. `create_all` only
#: creates missing *tables*, so a column added to a model would be absent from
#: an operator's existing data/netra.db and every ORM read of that table would
#: fail. These are applied additively at start-up rather than asking anyone to
#: delete their evidence database.
#: ponytail: a hand-kept list, not a migration tool. Its ceiling is additive,
#: nullable/defaulted columns on SQLite; a type change or a drop needs Alembic.
_ADDED_COLUMNS = [
    ("traffic_stats", "cumulative_total", "INTEGER DEFAULT 0"),
    ("traffic_stats", "loops_seen", "INTEGER DEFAULT 0"),
    ("mined_journeys", "min_similarity", "REAL DEFAULT 0.84"),
    ("mined_journeys", "truncated", "BOOLEAN DEFAULT 0"),
]


def _apply_added_columns() -> None:
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            if table not in existing:
                continue  # create_all just made it, with the column present
            have = {c["name"] for c in inspector.get_columns(table)}
            if column in have:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def init_db() -> None:
    from netra.core import models  # noqa: F401  (registers mappers)
    Base.metadata.create_all(engine)
    _apply_added_columns()
