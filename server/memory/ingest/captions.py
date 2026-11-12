"""Describe each kept frame of a session with a model.

    uv run python -m memory.ingest.captions <session id>            # every frame not captioned yet
    uv run python -m memory.ingest.captions <session id> --limit 3  # try a few first

For each frame the model returns a summary, the kind of space, objects placed relative to something that stays
put ("keys", "on", "kitchen counter"), and any readable text. Each call is logged in api_cost.
"""

import argparse
import base64
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from memory import db, llm
from memory.config import CAPTION_MODEL, DATA_DIR
from memory.ingest.recording import refresh_page

# Frames are shrunk to this width before upload. Image cost grows with size, and this still reads signs.
IMAGE_WIDTH = 768
PARALLEL_CALLS = 8

# A fixed list, so the same kind of space always gets the same label.
Setting = Literal[
    "kitchen", "bedroom", "bathroom", "living room", "dining room", "office", "classroom", "hallway", "garage",
    "store", "restaurant", "gym", "vehicle", "outdoors", "other",
]

Relation = Literal["on", "in", "under", "next to", "above", "in front of", "behind", "hanging on", "held by"]

INSTRUCTIONS = """\
You are looking at one frame from a camera worn on someone's glasses, so the view is what the wearer sees.
Describe it so that later they can ask what they saw and where things were.

Objects: list things a person might later look for, such as keys, a phone, a bag, a laptop, a cup, a book or a
tool. Skip walls, floors, cables and furniture. Place each object relative to an anchor: something nearby that
stays put, such as a counter, desk, shelf, bed or door. If the wearer is holding it, use "held by" and
"the wearer".

Report only what is visible. Do not guess the names of people."""


class SeenObject(BaseModel):
    name: str = Field(description="A plain noun for the object, lowercase, e.g. 'keys'")
    relation: Relation = Field(description="How the object relates to the anchor")
    anchor: str = Field(description="What it is on, in or next to, lowercase, e.g. 'kitchen counter'")


class SeenText(BaseModel):
    text: str = Field(description="Readable text, copied exactly")
    surface: str = Field(description="What it is written on, lowercase, e.g. 'whiteboard' or 'laptop screen'")


class Caption(BaseModel):
    summary: str = Field(description="One or two sentences: what is happening and what stands out")
    setting: Setting = Field(description="The kind of space this is; 'other' if none fits")
    objects: list[SeenObject] = Field(description="Up to 6 objects someone might later look for")
    texts: list[SeenText] = Field(description="Each piece of readable text; empty if there is none")


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

            print(f"  {Path(path).name}  [{caption.setting}]  {caption.summary}")

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

        refresh_page(conn, session_dir)

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
