"""List moments, or save a moment folder: python -m memory.db list | save <moment folder>."""

import sys
from pathlib import Path

import psycopg

from memory.config import clock
from memory.db.core import connect
from memory.db.moments import list_moments, save_moment


USAGE = "usage: python -m memory.db list | save <moment folder>"


def print_moments(rows: list[tuple]) -> None:
    print(f"{'moment':<21} {'source':<10} {'length':>7} {'frames':>7}  sound")

    for moment_id, source, _, duration, frames, has_sound, _, _, _ in rows:
        print(f"{moment_id:<21} {source:<10} {clock(duration):>7} {frames:>7}  {'yes' if has_sound else 'no'}")

    if not rows:
        print("(no moments yet)")


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "list"

    try:
        conn = connect()
    except psycopg.OperationalError:
        print("error: can't reach the database. Check DATABASE_URL in server/.env.", file=sys.stderr)
        return 1

    with conn:
        if command == "save" and len(sys.argv) == 3:
            saved = save_moment(conn, Path(sys.argv[2]))
            print(f"Saved {saved['frames']} frames. {saved['captions_kept']} captions kept, {saved['captions_lost']} lost.")
        elif command == "list":
            print_moments(list_moments(conn))
        else:
            print(USAGE, file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
