"""Embed a session's captions and transcript lines so they can be searched by meaning.

    uv run python -m memory.ingest.embeddings <session id>

Only frames and lines without an embedding are sent, so running it again costs nothing.
"""

import argparse
import sys

from memory import db, llm


def frame_text(summary: str, room: str, text_seen: str, objects: list[str]) -> str:
    """Join everything known about a frame into the one text that gets embedded and keyword-searched."""
    parts = [summary, f"Place: {room}."]

    if objects:
        parts.append("Objects: " + "; ".join(objects) + ".")

    if text_seen:
        parts.append(f"Text seen: {text_seen}")

    return " ".join(parts)


def embed_session(conn, session_id: str) -> tuple[int, int]:
    """Embed the session's captioned frames and transcript lines. Return how many of each were embedded."""
    frames = db.frames_without_embedding(conn, session_id)
    lines = db.lines_without_embedding(conn, session_id)

    frame_texts = [frame_text(summary, room, text_seen, objects) for _, summary, room, text_seen, objects in frames]
    line_texts = [text for _, text in lines]

    vectors = llm.embed(conn, frame_texts + line_texts, "embed") if frames or lines else []
    frame_vectors, line_vectors = vectors[: len(frames)], vectors[len(frames) :]

    with conn.transaction():
        db.save_frame_embeddings(conn, [(row[0], text, vector) for row, text, vector in zip(frames, frame_texts, frame_vectors)])
        db.save_line_embeddings(conn, [(row[0], vector) for row, vector in zip(lines, line_vectors)])

    return len(frames), len(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Embed a session's captions and transcript.")
    parser.add_argument("session_id")
    args = parser.parse_args()

    with db.connect() as conn:
        frames, lines = embed_session(conn, args.session_id)
        usd = db.step_cost(conn, "embed")

    print(f"Embedded {frames} frames and {lines} transcript lines.")
    print(f"Total spent on embedding so far: ${usd:.5f}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
