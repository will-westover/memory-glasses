"""Frames sent one at a time by the glasses, gathered into recordings and turned into videos when they stop."""

import subprocess
import time
from datetime import datetime
from pathlib import Path

from memory.config import DATA_DIR

GLASSES_DIR = DATA_DIR / "glasses"
IDLE_SECONDS = 60


def save_frame(recording: str, jpeg: bytes, now: float | None = None) -> Path:
    """Store one frame under its recording, named by milliseconds since the recording's first frame."""
    now = time.time() if now is None else now
    folder = GLASSES_DIR / recording
    started_file = folder / "started"

    folder.mkdir(parents=True, exist_ok=True)

    if not started_file.exists():
        started_file.write_text(repr(now))

    offset = round((now - float(started_file.read_text())) * 1000)
    path = folder / f"{offset:09d}.jpg"
    path.write_bytes(jpeg)

    return path


def frame_durations(names: list[str]) -> list[float]:
    """Return how long each frame should show: until the next one, and the last as long as the one before."""
    offsets = [int(Path(name).stem) / 1000 for name in names]
    gaps = [later - earlier for earlier, later in zip(offsets, offsets[1:])]

    return gaps + [gaps[-1] if gaps else 1.0]


def build_video(folder: Path) -> Path:
    """Join a recording's frames into an MP4 that keeps their timing and the time it started."""
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

    return video


def finished(now: float | None = None) -> list[Path]:
    """Return the recordings whose last frame arrived more than IDLE_SECONDS ago."""
    now = time.time() if now is None else now

    if not GLASSES_DIR.is_dir():
        return []

    done = []

    for folder in sorted(GLASSES_DIR.iterdir()):
        frames = list(folder.glob("*.jpg"))

        if (folder / "video.mp4").exists() or not frames:
            continue

        if now - max(frame.stat().st_mtime for frame in frames) > IDLE_SECONDS:
            done.append(folder)

    return done



def latest(now: float | None = None) -> dict | None:
    """Return how many photos the newest unfinished recording has and how long ago the last one arrived."""
    now = time.time() if now is None else now

    if not GLASSES_DIR.is_dir():
        return None

    open_folders = [folder for folder in GLASSES_DIR.iterdir() if not (folder / "video.mp4").exists()]
    frames_by_folder = {folder: list(folder.glob("*.jpg")) for folder in open_folders}
    frames_by_folder = {folder: frames for folder, frames in frames_by_folder.items() if frames}

    if not frames_by_folder:
        return None

    last = {folder: max(frame.stat().st_mtime for frame in frames) for folder, frames in frames_by_folder.items()}
    newest = max(last, key=last.get)

    return {"photos": len(frames_by_folder[newest]), "seconds_since_last": round(now - last[newest], 1)}
