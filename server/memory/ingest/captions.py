"""Describe each kept frame of a session with a model: a summary, the room, objects and any readable text.

    uv run python -m memory.ingest.captions <session id>            # every frame not captioned yet
    uv run python -m memory.ingest.captions <session id> --limit 3  # try a few first

Each call is logged in api_cost. The session's index.html is rewritten to show the captions.
"""

import argparse
import base64
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import CAPTION_MODEL, DATA_DIR
from memory.ingest.recording import write_page

# Frames are shrunk to this width before upload. Image cost grows with size, and this still reads signs.
IMAGE_WIDTH = 768
PARALLEL_CALLS = 8

INSTRUCTIONS = """\
You are looking at one frame from a camera worn on someone's glasses. Describe it so that later they can
ask what they saw and where things were. Report only what is visible. Do not guess names of people."""


class SeenObject(BaseModel):
    name: str = Field(description="A plain noun for the object, lowercase, e.g. 'laptop'")
    location: str = Field(description="Where it is in the scene, e.g. 'on the desk, left of the monitor'")


class Caption(BaseModel):
    summary: str = Field(description="One or two sentences: what is happening and what stands out")
    room: str = Field(description="The kind of place, e.g. 'kitchen' or 'office', or 'unknown'")
    objects: list[SeenObject] = Field(description="Up to 8 objects someone might later look for")
    text_seen: str = Field(description="Readable text in the frame, copied exactly, or an empty string")


def small_jpeg(path: Path) -> bytes:
    """Return the image shrunk to IMAGE_WIDTH pixels wide, as JPEG bytes."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"scale={IMAGE_WIDTH}:-2", "-q:v", "4",
         "-f", "image2pipe", "-c:v", "mjpeg", "-"],
        capture_output=True, check=True,
    )

    return result.stdout


def caption_image(path: Path, model: str = CAPTION_MODEL):
    """Ask the model to describe one image. Return (Caption, usage)."""
    encoded = base64.b64encode(small_jpeg(path)).decode()

    response = llm.client().responses.parse(
        model=model,
        instructions=INSTRUCTIONS,
        input=[{
            "role": "user",
            "content": [{"type": "input_image", "image_url": f"data:image/jpeg;base64,{encoded}"}],
        }],
        text_format=Caption,
        reasoning={"effort": "none"},
    )

    return response.output_parsed, response.usage


def caption_session(conn, session_id: str, limit: int | None = None, model: str = CAPTION_MODEL) -> dict:
    """Caption the session's frames that have no caption yet. Return totals for the run."""
    frames = db.frames_without_caption(conn, session_id, limit)
    started = time.monotonic()
    totals = {"frames": 0, "failed": 0, "usd": 0.0, "input_tokens": 0, "output_tokens": 0}

    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        jobs = {pool.submit(caption_image, DATA_DIR / path, model): (frame_id, path) for frame_id, path in frames}

        for job in as_completed(jobs):
            frame_id, path = jobs[job]

            try:
                caption, usage = job.result()
            except Exception as error:  # one bad frame shouldn't stop the rest
                totals["failed"] += 1
                print(f"  failed {Path(path).name}: {error}", file=sys.stderr)
                continue

            with conn.transaction():
                db.save_caption(conn, frame_id, caption)
                totals["usd"] += llm.log_cost(conn, "caption", model, usage, frame_id)

            totals["frames"] += 1
            totals["input_tokens"] += usage.input_tokens
            totals["output_tokens"] += usage.output_tokens

            print(f"  {Path(path).name}  {caption.summary}")

    totals["seconds"] = time.monotonic() - started

    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description="Caption a session's frames.")
    parser.add_argument("session_id")
    parser.add_argument("--limit", type=int, help="caption at most this many frames")
    parser.add_argument("--model", default=CAPTION_MODEL)
    args = parser.parse_args()

    session_dir = DATA_DIR / "sessions" / args.session_id
    if not (session_dir / "manifest.json").exists():
        print(f"error: no session at {session_dir}", file=sys.stderr)
        return 1

    with db.connect() as conn:
        totals = caption_session(conn, args.session_id, args.limit, args.model)

        manifest = json.loads((session_dir / "manifest.json").read_text())
        write_page(session_dir, manifest, db.captions(conn, args.session_id))

        remaining = len(db.frames_without_caption(conn, args.session_id))

    print(f"\nCaptioned {totals['frames']} frames in {totals['seconds']:.0f} s with {args.model}.")
    if totals["frames"]:
        print(f"Tokens: {totals['input_tokens']:,} in, {totals['output_tokens']:,} out.")
        print(f"Cost: ${totals['usd']:.4f} (${totals['usd'] / totals['frames']:.5f} per frame).")
    if totals["failed"]:
        print(f"{totals['failed']} failed; run again to retry them.")
    print(f"{remaining} frames still without a caption.")
    print(f"Look at: {session_dir / 'index.html'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
