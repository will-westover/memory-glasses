"""Frames sent one at a time by the glasses, gathered into recordings and turned into videos when they stop."""

import re
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from memory import db, storage
from memory.config import DATA_DIR

GLASSES_DIR = DATA_DIR / "glasses"
IDLE_SECONDS = 300
MAX_FRAME_BYTES = 2_000_000
UPLOAD_NAME = re.compile(r"^[0-9a-f]{32}$")


def frame_name(started: float, taken: float) -> str:
    """Name a frame by the milliseconds between the recording's first frame and when this one was taken."""
    return f"{max(0, round((taken - started) * 1000)):09d}.jpg"


def place(conn, recording: str, taken: float | None) -> Path:
    """Count a frame in its recording and return where it belongs, by the time it was taken when that is known."""
    now = time.time()
    taken = now if taken is None else min(taken, now)
    name, started = db.note_frame(conn, recording, datetime.fromtimestamp(taken, UTC), datetime.fromtimestamp(now, UTC))

    return GLASSES_DIR / name / frame_name(started.timestamp(), taken)


def save_frame(conn, recording: str, jpeg: bytes, taken: float | None = None) -> Path:
    """Store one frame sent to the server under its recording."""
    path = place(conn, recording, taken)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg)

    storage.save(path)

    return path


def upload_slot(recording: str) -> dict:
    """Return a link the glasses can PUT their next frame to in R2, and its name."""
    name = uuid.uuid4().hex

    return {"upload_url": storage.upload_url(GLASSES_DIR / recording / f"{name}.pending"), "upload_name": name}


def arrived(conn, recording: str, name: str, taken: float | None = None) -> Path | None:
    """Give a frame the glasses put in R2 its place in the recording, or None if it isn't there or is too big."""
    pending = GLASSES_DIR / recording / f"{name}.pending"
    size = storage.size(pending)

    if size is None:
        return None

    if size > MAX_FRAME_BYTES:
        storage.delete(pending)
        return None

    path = place(conn, recording, taken)
    storage.copy(pending, path)
    storage.delete(pending)

    return path


def frame_durations(names: list[str]) -> list[float]:
    """Return how long each frame should show: until the next one, and the last as long as the one before."""
    offsets = [int(Path(name).stem) / 1000 for name in names]
    gaps = [later - earlier for earlier, later in zip(offsets, offsets[1:])]

    return gaps + [gaps[-1] if gaps else 1.0]


def build_video(folder: Path, started: datetime) -> Path:
    """Join a recording's frames into an MP4 that keeps their timing and the time it started."""
    storage.fetch_folder(folder)
    frames = sorted(path.name for path in folder.glob("*.jpg"))

    playlist = folder / "frames.txt"
    lines = [f"file '{name}'\nduration {seconds:.3f}" for name, seconds in zip(frames, frame_durations(frames))]
    playlist.write_text("\n".join(lines + [f"file '{frames[-1]}'"]) + "\n")

    video = folder / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(playlist),
            "-fps_mode",
            "vfr",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-metadata",
            f"creation_time={started.astimezone().isoformat()}",
            str(video),
        ],
        check=True,
    )

    storage.save(video)

    return video
