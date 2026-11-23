"""Describe each kept frame of a moment with a model."""

import argparse
import base64
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pydantic import BaseModel, Field

from server import db, llm, storage
from server.config import CAPTION_MODEL, DATA_DIR

IMAGE_WIDTH = 1080
PARALLEL_CALLS = 8
SPOKEN_WINDOW_SECONDS = 30

INSTRUCTIONS = """\
You are looking at one frame from a camera worn on someone's glasses, so the view is what the wearer sees.
Describe it thoroughly and accurately. Your description is the only record of this moment, and it will be used
later to answer any kind of question about what the wearer saw.

Objects: list every separate thing you can see, large or small, including things in the background. Name each
one as specifically as you can tell from the picture: the kind of plant, food, animal or device, and the brand or
model when it is readable. Skip walls, floors and built-in furniture. For each, say where it is in a short phrase of
your own, give the details that would tell it apart from a similar thing (colour, material, pattern, size, wear,
any writing on it), and mark the box around it.

Readable text: copy it exactly. When words spoken around this moment are given, they can help read blurry text (a
slide that says "dendrogram" while the speaker says "dendrogram"), but never report text that is not visible.

The glasses are not always worn. If the view is fixed, very low or tilted, or a person faces the camera from close by,
the glasses have probably been set down; say so in the summary, and remember that the person facing them may be the
wearer.

Report only what is visible. Do not guess the names of people."""


class SeenObject(BaseModel):
    name: str = Field(description="The most specific plain name, lowercase, e.g. 'olive tree' or 'water bottle'")
    location: str = Field(description="Where it is, as a short lowercase phrase, e.g. 'on the kitchen island'")
    details: str = Field(description="What would tell it apart: colour, material, pattern, size, writing; short")
    box: list[float] = Field(
        description="Left, top, width and height as fractions of the picture from 0 to 1, e.g. [0.4, 0.2, 0.1, 0.3]"
    )


class SeenText(BaseModel):
    text: str = Field(description="Readable text, copied exactly")
    surface: str = Field(description="What it is written on, lowercase, e.g. 'whiteboard' or 'laptop screen'")


class Caption(BaseModel):
    summary: str = Field(description="One or two sentences: what is happening, where, and what stands out")
    objects: list[SeenObject] = Field(description="Every separate object visible, up to 20")
    texts: list[SeenText] = Field(description="Each piece of readable text; empty if there is none")


def small_jpeg(path: Path) -> bytes:
    """Return the image as JPEG bytes, at most IMAGE_WIDTH pixels wide."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-vf",
            f"scale='min({IMAGE_WIDTH},iw)':-2",
            "-q:v",
            "3",
            "-f",
            "image2pipe",
            "-c:v",
            "mjpeg",
            "-",
        ],
        capture_output=True,
        check=True,
    )

    return result.stdout


def image_link(path: Path) -> str:
    """Return how the model gets the image: a short-lived R2 link when R2 holds it, else the bytes inline."""
    if storage.stored_size(path) is not None:
        return storage.download_url(path, "image/jpeg", seconds=3600)

    return f"data:image/jpeg;base64,{base64.b64encode(small_jpeg(path)).decode()}"


def spoken_near(lines: list[dict], offset: float) -> str:
    """Return what was said in the transcript lines that overlap the minute around a frame."""
    near = [
        line
        for line in lines
        if line["start"] <= offset + SPOKEN_WINDOW_SECONDS and line["end"] >= offset - SPOKEN_WINDOW_SECONDS
    ]

    return " ".join(line["text"] for line in near)


def caption_image(path: Path, model: str = CAPTION_MODEL, spoken: str = ""):
    """Ask the model to describe one image, with what was said around it if anything. Return (Caption, usage)."""
    image_url = image_link(path)
    said = [{"type": "input_text", "text": f"Said around this moment: {spoken}"}] if spoken else []

    response = llm.client().responses.parse(
        model=model,
        instructions=INSTRUCTIONS,
        input=[
            {
                "role": "user",
                "content": said + [{"type": "input_image", "image_url": image_url}],
            }
        ],
        text_format=Caption,
        reasoning={"effort": "none"},
    )

    return response.output_parsed, response.usage


def save_result(conn, totals: dict, frame_id: int, caption: Caption, usage, model: str) -> None:
    """Store one frame's caption and its cost, and add it to the run's totals."""
    with conn.transaction():
        db.save_caption(conn, frame_id, caption)
        totals["usd"] += llm.log_cost(conn, "caption", model, usage, frame_id)

    totals["frames"] += 1
    totals["input_tokens"] += usage.input_tokens
    totals["output_tokens"] += usage.output_tokens


def caption_moment(conn, moment_id: str, limit: int | None = None, model: str = CAPTION_MODEL) -> dict:
    """Caption the moment's frames that have no caption yet, several at a time."""
    llm.require_price(model)
    storage.fetch_folder(DATA_DIR / "moments" / moment_id)

    frames = db.frames_without_caption(conn, moment_id, limit)
    lines = db.transcript_lines(conn, moment_id)
    started = time.monotonic()
    totals = {"frames": 0, "failed": 0, "usd": 0.0, "input_tokens": 0, "output_tokens": 0}

    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        jobs = {
            pool.submit(caption_image, DATA_DIR / path, model, spoken_near(lines, float(offset))): (frame_id, path)
            for frame_id, path, offset in frames
        }

        for job in as_completed(jobs):
            frame_id, path = jobs[job]

            try:
                caption, usage = job.result()
            except Exception as error:
                totals["failed"] += 1
                print(f"  failed {Path(path).name}: {error}", file=sys.stderr)
                continue

            save_result(conn, totals, frame_id, caption, usage, model)
            print(f"  {Path(path).name}  {caption.summary}")

    totals["seconds"] = time.monotonic() - started

    return totals


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Caption a moment's frames.")
    parser.add_argument("moment_id")
    parser.add_argument("--limit", type=int, help="caption at most this many frames")
    parser.add_argument("--redo", action="store_true", help="pay to caption every frame again")
    parser.add_argument("--model", default=CAPTION_MODEL)

    return parser.parse_args()


def print_report(totals: dict, remaining: int, model: str, moment_dir: Path) -> None:
    print(f"\nCaptioned {totals['frames']} frames in {totals['seconds']:.0f} s with {model}.")

    if totals["frames"]:
        print(f"Tokens: {totals['input_tokens']:,} in, {totals['output_tokens']:,} out.")
        print(f"Cost: ${totals['usd']:.4f} (${totals['usd'] / totals['frames']:.5f} per frame).")

    if totals["failed"]:
        print(f"{totals['failed']} failed; run again to retry them.")

    print(f"{remaining} frames still without a caption.")
    print(f"Look at: {moment_dir / 'index.html'}")


def main() -> int:
    args = parse_arguments()
    moment_dir = DATA_DIR / "moments" / args.moment_id

    if storage.local_copy(moment_dir / "manifest.json") is None:
        print(f"error: no moment at {moment_dir}", file=sys.stderr)
        return 1

    with db.connect() as conn:
        if args.redo:
            db.clear_captions(conn, args.moment_id)

        totals = caption_moment(conn, args.moment_id, args.limit, args.model)

        remaining = len(db.frames_without_caption(conn, args.moment_id))

    print_report(totals, remaining, args.model, moment_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
