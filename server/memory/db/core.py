"""Connecting to the database and keeping its tables up to date."""

import threading

import psycopg
from psycopg_pool import ConnectionPool

from memory.config import SERVER_DIR, require_env


MIGRATIONS_DIR = SERVER_DIR / "migrations"


def connect() -> psycopg.Connection:
    """Open a connection and make sure the tables are up to date."""
    conn = psycopg.connect(require_env("DATABASE_URL"), connect_timeout=10)
    migrate(conn)

    return conn


POOL = {}


POOL_LOCK = threading.Lock()


POOL_SIZE = 10


POOL_IDLE_SECONDS = 120


def pool() -> ConnectionPool:
    """Return the connection pool the API shares, opening it and bringing the tables up to date the first time."""
    with POOL_LOCK:
        if "pool" not in POOL:
            with connect():
                pass

            POOL["pool"] = ConnectionPool(
                require_env("DATABASE_URL"),
                min_size=0,
                max_size=POOL_SIZE,
                max_idle=POOL_IDLE_SECONDS,
                kwargs={"connect_timeout": 10},
                check=ConnectionPool.check_connection,
            )

    return POOL["pool"]


def migrate(conn: psycopg.Connection) -> list[str]:
    """Run the SQL files in migrations/ that haven't run yet."""
    conn.execute(
        "create table if not exists schema_migration"
        " (name text primary key, applied_at timestamptz not null default now())"
    )

    done = {name for (name,) in conn.execute("select name from schema_migration")}
    ran = []

    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if path.name not in done:
            conn.execute(path.read_text())
            conn.execute("insert into schema_migration (name) values (%s)", (path.name,))
            ran.append(path.name)

    conn.commit()

    return ran
