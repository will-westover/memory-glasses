"""Turn a recorded video into a session folder.

    uv run python -m memory.ingest.recording path/to/video.mov

Writes data/sessions/<start time>/ with:
    frames/        one frame every few seconds, near-duplicates dropped
    audio.wav      mono 16 kHz, ready for speech-to-text (missing if the video has no sound)
    manifest.json  every kept frame with its time and fingerprint
    index.html     the kept frames on one page, to check by eye

The session is also saved to the database when it's running.
"""

import argparse
import html
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import psycopg

from memory import db
from memory.config import DATA_DIR
from memory.ingest.fingerprint import (
    DEFAULT_MAX_DISTANCE,
    THUMB_BYTES,
    THUMB_HEIGHT,
    THUMB_WIDTH,
    fingerprint,
    indexes_to_keep,
)

DEFAULT_SECONDS_PER_FRAME = 2.0


class IngestError(Exception):
    """The recording could not be turned into a session."""


def run(command: list[str]) -> bytes:
    """Run a command and return what it printed, or raise IngestError with its error text."""
    result = subprocess.run(command, capture_output=True)

    if result.returncode != 0:
        raise IngestError(f"{command[0]} failed: {result.stderr.decode(errors='replace').strip()}")

    return result.stdout


def probe(video: Path) -> tuple[datetime, float, bool]:
    """Return when the recording started (local time), how long it is in seconds, and whether it has sound."""
    info = json.loads(
        run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)])
    )
    duration = float(info["format"].get("duration", 0))
    has_audio = any(stream["codec_type"] == "audio" for stream in info["streams"])

    created = info["format"].get("tags", {}).get("creation_time")
    if created:
        started = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone()
    else:
        # No start time in the file: the file's modified time is when recording ended.
        ended = datetime.fromtimestamp(video.stat().st_mtime).astimezone()
        started = ended - timedelta(seconds=duration)

    return started, duration, has_audio


def extract_frames(video: Path, frames_dir: Path, seconds_per_frame: float) -> list[int]:
    """Write one JPEG every `seconds_per_frame` seconds into frames_dir and return each one's fingerprint.

    One ffmpeg pass writes both the JPEGs and the tiny grey thumbnails, so the two always line up.
    """
    frames_dir.mkdir(parents=True)

    with tempfile.TemporaryDirectory() as scratch:
        thumbs = Path(scratch) / "thumbs.grey"
        graph = (
            f"[0:v]fps=1/{seconds_per_frame},split[full][small];"
            f"[small]scale={THUMB_WIDTH}:{THUMB_HEIGHT},format=gray[thumb]"
        )

        run([
            "ffmpeg", "-v", "error", "-i", str(video),
            "-filter_complex", graph,
            "-map", "[full]", "-q:v", "3", str(frames_dir / "%06d.jpg"),
            "-map", "[thumb]", "-f", "rawvideo", str(thumbs),
        ])

        grey = thumbs.read_bytes()

    count = len(grey) // THUMB_BYTES
    written = len(list(frames_dir.glob("*.jpg")))
    if count != written:
        raise IngestError(f"ffmpeg wrote {written} frames but {count} thumbnails")

    return [fingerprint(grey[i * THUMB_BYTES : (i + 1) * THUMB_BYTES]) for i in range(count)]


def extract_audio(video: Path, audio_path: Path) -> None:
    """Write the recording's sound as a mono 16 kHz WAV file."""
    run([
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio_path),
    ])


def clock(seconds: float) -> str:
    """Format seconds as m:ss."""
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def caption_html(caption: dict | None) -> str:
    """Return the HTML shown under a frame for its caption, or nothing if it has none."""
    if not caption:
        return ""

    parts = [f'<p>{html.escape(caption["summary"])}</p>', f'<p class="dim">Room: {html.escape(caption["room"])}</p>']
    if caption["objects"]:
        parts.append(f'<p class="dim">{html.escape(", ".join(caption["objects"]))}</p>')
    if caption["text_seen"]:
        parts.append(f'<p class="dim">Text: {html.escape(caption["text_seen"])}</p>')

    return "".join(parts)


def write_page(session_dir: Path, manifest: dict, captions: dict[str, dict] | None = None) -> None:
    """Write index.html: the kept frames on one page, with captions (keyed by file name) where they exist."""
    captions = captions or {}

    cards = "\n".join(
        f'<figure><img src="{html.escape(frame["file"])}" loading="lazy">'
        f'<figcaption>{clock(frame["offset_seconds"])}'
        f'{caption_html(captions.get(Path(frame["file"]).name))}</figcaption></figure>'
        for frame in manifest["frames"]
    )

    audio = '<audio controls src="audio.wav"></audio>' if manifest["audio"] else "<p>No sound in this recording.</p>"

    page = f"""<!doctype html>
<meta charset="utf-8">
<title>Session {html.escape(manifest["id"])}</title>
<style>
  body {{ font: 15px system-ui; margin: 24px; background: #111; color: #eee; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }}
  figure {{ margin: 0; }}
  img {{ width: 100%; border-radius: 6px; display: block; }}
  figcaption {{ color: #aaa; padding-top: 4px; }}
  figcaption p {{ margin: 4px 0 0; color: #eee; }}
  figcaption p.dim {{ color: #999; font-size: 13px; }}
</style>
<h1>Session {html.escape(manifest["id"])}</h1>
<p>From {html.escape(manifest["video"])}, {clock(manifest["duration_seconds"])} long.
Kept {manifest["frames_kept"]} of {manifest["frames_sampled"]} frames
(one sampled every {manifest["seconds_per_frame"]:g} seconds; the rest looked the same as the frame before).</p>
{audio}
<div class="grid">
{cards}
</div>
"""

    (session_dir / "index.html").write_text(page)


def ingest(
    video: Path,
    data_dir: Path = DATA_DIR,
    seconds_per_frame: float = DEFAULT_SECONDS_PER_FRAME,
    max_distance: int = DEFAULT_MAX_DISTANCE,
    replace: bool = False,
) -> Path:
    """Turn a video into a session folder and return the folder's path."""
    if not video.is_file():
        raise IngestError(f"no such file: {video}")

    started, duration, has_audio = probe(video)
    session_id = started.strftime("%Y-%m-%dT%H-%M-%S")
    session_dir = data_dir / "sessions" / session_id
    if session_dir.exists():
        if not replace:
            raise IngestError(f"{session_dir} already exists. Pass --replace to build it again.")
        shutil.rmtree(session_dir)

    frames_dir = session_dir / "frames"
    fingerprints = extract_frames(video, frames_dir, seconds_per_frame)
    kept = set(indexes_to_keep(fingerprints, max_distance))

    frames = []
    for index, path in enumerate(sorted(frames_dir.glob("*.jpg"))):
        if index not in kept:
            path.unlink()
            continue

        offset = index * seconds_per_frame
        frames.append({
            "file": f"frames/{path.name}",
            "offset_seconds": offset,
            "captured_at": (started + timedelta(seconds=offset)).isoformat(),
            "fingerprint": f"{fingerprints[index]:016x}",
        })

    if has_audio:
        extract_audio(video, session_dir / "audio.wav")

    manifest = {
        "id": session_id,
        "source": "recording",
        "video": video.name,
        "started_at": started.isoformat(),
        "duration_seconds": duration,
        "seconds_per_frame": seconds_per_frame,
        "frames_sampled": len(fingerprints),
        "frames_kept": len(frames),
        "audio": "audio.wav" if has_audio else None,
        "frames": frames,
    }

    (session_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    write_page(session_dir, manifest)

    return session_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Turn a recorded video into a session folder.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--seconds-per-frame", type=float, default=DEFAULT_SECONDS_PER_FRAME)
    parser.add_argument(
        "--max-distance", type=int, default=DEFAULT_MAX_DISTANCE,
        help="frames differing in this many fingerprint bits or fewer are dropped (0 to 64)",
    )
    parser.add_argument("--replace", action="store_true", help="rebuild the session if it already exists")
    args = parser.parse_args()

    try:
        session_dir = ingest(
            args.video,
            seconds_per_frame=args.seconds_per_frame,
            max_distance=args.max_distance,
            replace=args.replace,
        )
    except IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    manifest = json.loads((session_dir / "manifest.json").read_text())
    print(f"Kept {manifest['frames_kept']} of {manifest['frames_sampled']} frames.")
    print(f"Sound: {'audio.wav' if manifest['audio'] else 'none'}")
    print(f"Look at: {session_dir / 'index.html'}")

    try:
        with db.connect() as conn:
            db.save_session(conn, session_dir)
        print("Saved to the database.")
    except psycopg.OperationalError:
        print("Not saved: the database isn't running. Start it with: docker compose up -d --wait")

    return 0


if __name__ == "__main__":
    sys.exit(main())
