"""List, save or delete moments: python -m memory.db list | save <moment folder> | delete <moment id>..."""

import shutil
import sys
from pathlib import Path

import psycopg

from memory import storage
from memory.config import DATA_DIR, minutes_seconds
from memory.db.core import connect
from memory.db.moments import delete_moment, list_moments, save_moment
from memory.db.places import drop_unused_rooms_and_places


USAGE = "usage: python -m memory.db list | save <moment folder> | delete <moment id>..."


def print_moments(rows: list[tuple]) -> None:
    print(f"{'moment':<21} {'source':<10} {'length':>7} {'frames':>7}  sound")

    for moment_id, source, _started_at, duration, frames, has_sound, _lat, _lon, _title in rows:
        print(
            f"{moment_id:<21} {source:<10} {minutes_seconds(duration):>7} {frames:>7}  {'yes' if has_sound else 'no'}"
        )

    if not rows:
        print("(no moments yet)")


def delete_moments(conn: psycopg.Connection, moment_ids: list[str]) -> None:
    """Delete moments from the database, then their files from R2 and this Mac."""
    for moment_id in moment_ids:
        recordings = delete_moment(conn, moment_id)
        drop_unused_rooms_and_places(conn)
        conn.commit()

        for folder in [DATA_DIR / "moments" / moment_id] + [DATA_DIR / "glasses" / name for name in recordings]:
            storage.delete_folder(folder)
            shutil.rmtree(folder, ignore_errors=True)

        print(f"Deleted {moment_id} and {len(recordings)} glasses recording(s).")


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
            print(
                f"Saved {saved['frames']} frames. {saved['captions_kept']} captions kept, {saved['captions_lost']} lost."
            )
        elif command == "delete" and len(sys.argv) > 2:
            delete_moments(conn, sys.argv[2:])
        elif command == "list":
            print_moments(list_moments(conn))
        else:
            print(USAGE, file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
