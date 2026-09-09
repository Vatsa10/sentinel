"""Database engine and session handling."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from netra.config import DB_URL


class Base(DeclarativeBase):
    pass


_connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=_connect_args, pool_pre_ping=True)


if DB_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        # WAL lets the console read while the writer thread commits detection
        # batches; the default rollback journal makes every reader wait for the
        # writer and surfaces as "database is locked" 500s under load. The busy
        # timeout covers the short exclusive window a WAL checkpoint still needs.
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()
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
    # Defaults false, which is the honest reading of every row already in an
    # operator's store: those scene times were anchored on a single overlay
    # reading and are not evidence of when anything happened.
    ("detections", "scene_time_corroborated", "BOOLEAN DEFAULT 0"),
    # Nullable rather than defaulted to 1: an existing row's plate was read an
    # unknown number of times, and claiming one vote would be inventing a fact.
    ("detections", "plate_votes", "INTEGER"),
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
