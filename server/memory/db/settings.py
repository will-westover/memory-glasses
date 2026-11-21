"""The user's settings."""

import psycopg


def setting(conn: psycopg.Connection, name: str, default):
    """Return a saved setting, or the default if it has never been set."""
    row = conn.execute("select value from user_settings where name = %s", (name,)).fetchone()

    return row[0] if row else default


def save_setting(conn: psycopg.Connection, name: str, value) -> None:
    """Save a setting."""
    conn.execute(
        "insert into user_settings (name, value) values (%s, %s) on conflict (name) do update set value = excluded.value",
        (name, str(value)),
    )
