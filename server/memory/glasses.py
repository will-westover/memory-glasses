"""Frames sent one at a time by the glasses, gathered into recordings and turned into videos when they stop."""

import subprocess
import time
from datetime import datetime
from pathlib import Path

from memory import storage
from memory.config import DATA_DIR

GLASSES_DIR = DATA_DIR / "glasses"
IDLE_SECONDS = 60
LIST_SECONDS = 30
REMOTE = {"at": float("-inf"), "files": {}}


def save_frame(recording: str, jpeg: bytes, now: float | None = None) -> Path:
    """Store one frame under its recording, named by milliseconds since the recording's first frame."""
    now = time.time() if now is None else now
    folder = GLASSES_DIR / recording
    started_file = folder / "started"

    folder.mkdir(parents=True, exist_ok=True)

    if storage.local(started_file) is None:
        started_file.write_text(repr(now))
        storage.save(started_file)

    offset = round((now - float(started_file.read_text())) * 1000)
    path = folder / f"{offset:09d}.jpg"
    path.write_bytes(jpeg)

    storage.save(path)

    return path


def frame_durations(names: list[str]) -> list[float]:
    """Return how long each frame should show: until the next one, and the last as long as the one before."""
    offsets = [int(Path(name).stem) / 1000 for name in names]
    gaps = [later - earlier for earlier, later in zip(offsets, offsets[1:])]

    return gaps + [gaps[-1] if gaps else 1.0]


def build_video(folder: Path) -> Path:
    """Join a recording's frames into an MP4 that keeps their timing and the time it started."""
    storage.fetch_folder(folder)
    frames = sorted(path.name for path in folder.glob("*.jpg"))
    started = datetime.fromtimestamp(float((folder / "started").read_text())).astimezone()

    playlist = folder / "frames.txt"
    lines = [f"file '{name}'\nduration {seconds:.3f}" for name, seconds in zip(frames, frame_durations(frames))]
    playlist.write_text("\n".join(lines + [f"file '{frames[-1]}'"]) + "\n")

    video = folder / "video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(playlist),
         "-fps_mode", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-metadata", f"creation_time={started.isoformat()}", str(video)],
        check=True,
    )

    storage.save(video)

    return video


def recordings() -> dict[str, dict]:
    """Return each recording's photo count, newest photo time and whether it has become a video, locally and in R2."""
    files = {}

    if GLASSES_DIR.is_dir():
        for path in GLASSES_DIR.glob("*/*"):
            files[f"{path.parent.name}/{path.name}"] = path.stat().st_mtime

    for name, modified in remote_files().items():
        files.setdefault(name, modified)

    return summarise(files)


def remote_files() -> dict[str, float]:
    """Return the glasses files in R2 by recording/file, listing the bucket at most every LIST_SECONDS."""
    if not storage.enabled():
        return {}

    if time.monotonic() - REMOTE["at"] > LIST_SECONDS:
        listed = storage.listing(storage.key(GLASSES_DIR) + "/")
        REMOTE.update(at=time.monotonic(), files={name.split("/", 1)[1]: item["modified"]
                                                  for name, item in listed.items()})

    return REMOTE["files"]


def summarise(files: dict[str, float]) -> dict[str, dict]:
    """Group files named recording/file by recording: photos, when the last arrived, and whether there is a video."""
    found = {}

    for name, modified in files.items():
        recording, file = name.split("/", 1)
        entry = found.setdefault(recording, {"photos": 0, "last": None, "done": False})

        if file == "video.mp4":
            entry["done"] = True
        elif file.endswith(".jpg"):
            entry["photos"] += 1
            entry["last"] = modified if entry["last"] is None else max(entry["last"], modified)

    return {recording: entry for recording, entry in found.items() if entry["photos"]}


def finished(now: float | None = None) -> list[Path]:
    """Return the recordings whose last frame arrived more than IDLE_SECONDS ago."""
    now = time.time() if now is None else now

    return [GLASSES_DIR / recording for recording, entry in sorted(recordings().items())
            if not entry["done"] and now - entry["last"] > IDLE_SECONDS]


def latest(now: float | None = None) -> dict | None:
    """Return how many photos the newest unfinished recording has and how long ago the last one arrived."""
    now = time.time() if now is None else now
    open_ones = [entry for entry in recordings().values() if not entry["done"]]

    if not open_ones:
        return None

    newest = max(open_ones, key=lambda entry: entry["last"])

    return {"photos": newest["photos"], "seconds_since_last": round(now - newest["last"], 1)}
