"""Run every step on a recording: frames, captions, transcript, faces, rooms and facts.

uv run python -m server.ingest.pipeline path/to/video.mov --frames-per-minute 30
"""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from server import db, voices
from server.shared.config import FRAMES_PER_MINUTE
from server.ingest import places
from server.ingest import captions, faces, facts, recording, transcript

STEPS = ("Picking frames", "Transcribing", "Describing frames", "Finding faces", "Finding rooms", "Building facts")


def process(
    video: Path,
    frames_per_minute: float = FRAMES_PER_MINUTE,
    extras: dict[str, Path] | None = None,
    replace: bool = False,
    reuse_frames: bool = False,
    place_id: int | None = None,
    source: str | None = None,
    on_step: Callable[[str], None] = lambda step: None,
) -> str:
    """Turn a video into a searchable moment and return its id."""
    on_step(STEPS[0])
    moment_dir = recording.ingest(
        video,
        frames_per_minute=frames_per_minute,
        replace=replace,
        extras=extras,
        reuse_frames=reuse_frames,
        source=source,
    )
    moment_id = moment_dir.name

    with db.connect() as conn:
        db.save_moment(conn, moment_dir)
        db.locate_from_phone(conn, moment_id)
        conn.commit()

        on_step(STEPS[1])
        transcript.transcribe_moment(conn, moment_id)
        voices.spread_names(conn, moment_id)
        conn.commit()

        on_step(STEPS[2])
        captions.caption_moment(conn, moment_id)
        conn.commit()

        on_step(STEPS[3])
        faces.find_faces(conn, moment_id)

        on_step(STEPS[4])
        if not reuse_frames or db.moment_place(conn, moment_id) is None:
            places.assign_place(conn, moment_id, moment_dir, place_id)

        places.label_rooms(conn, moment_id)
        conn.commit()

        on_step(STEPS[5])
        facts.build_facts(conn, moment_id)

    return moment_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Run every step on a recording.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--frames-per-minute", type=float)
    parser.add_argument("--replace", action="store_true", help="rebuild the moment if it already exists")
    args = parser.parse_args()

    rate = recording.saved_frames_per_minute() if args.frames_per_minute is None else args.frames_per_minute

    try:
        moment_id = process(args.video, rate, replace=args.replace, on_step=lambda step: print(f"{step}…"))
    except recording.IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(f"Done: moment {moment_id}.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
