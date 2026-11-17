"""The people whose faces were found, and their names.

    uv run python -m memory.people list
    uv run python -m memory.people name <id> "Sam"
    uv run python -m memory.people merge <id> <into id>
    uv run python -m memory.people forget <id>
"""

import argparse
import sys

from memory import db


def main() -> int:
    parser = argparse.ArgumentParser(description="Name, merge and forget people.")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("list")

    name = commands.add_parser("name")
    name.add_argument("person_id", type=int)
    name.add_argument("name")

    merge = commands.add_parser("merge")
    merge.add_argument("person_id", type=int)
    merge.add_argument("into", type=int)

    forget = commands.add_parser("forget")
    forget.add_argument("person_id", type=int)
    args = parser.parse_args()

    with db.connect() as conn:
        if args.command == "name":
            db.name_person(conn, args.person_id, args.name)
        elif args.command == "merge":
            db.merge_people(conn, args.person_id, args.into)
        elif args.command == "forget":
            db.forget_person(conn, args.person_id)

        for person in db.people(conn):
            print(f"{person['id']:>4}  {person['name'] or '(unnamed)':<20} {person['faces']:>4} faces"
                  f"  last seen {person['last_seen'].astimezone():%a %d %b %H:%M}")

    print("Rebuild facts for a session to use new names: uv run python -m memory.ingest.facts <session id>")

    return 0


if __name__ == "__main__":
    sys.exit(main())
