"""Ask a question from the command line."""

import argparse
import sys

from server import db
from server.agent import ask_agent, ask_plain
from server.config import AGENT_MODEL, minutes_seconds


def print_result(result: dict) -> None:
    print(result["answer"] if result["found"] else f"(not found) {result['answer']}")

    if result["sources"]:
        print("\nSources:")

    for fact in result["sources"]:
        print(
            f"  [{fact['id']}] {fact['at'].astimezone():%a %d %b %H:%M:%S}, {minutes_seconds(fact['offset_seconds'])} into"
            f" {fact['moment_id']}\n      {fact['text']}"
        )

        if fact["image_path"]:
            print(f"      {fact['image_path']}")

    if result["calls"]:
        print("\nTool calls:")

    for name, arguments in result["calls"]:
        given = ", ".join(f"{key}={value!r}" for key, value in arguments.items() if value is not None)
        print(f"  {name}({given})")

    print(f"\n{result['model_calls']} model calls, {result['seconds']:.1f} s, ${result['usd']:.5f}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Answer a question from what the glasses recorded.")
    parser.add_argument("question")
    parser.add_argument("--plain", action="store_true", help="one search and one model call, with no tools")
    parser.add_argument("--model", default=AGENT_MODEL)
    args = parser.parse_args()

    with db.connect() as conn:
        result = (ask_plain if args.plain else ask_agent)(conn, args.question, args.model)

    print_result(result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
