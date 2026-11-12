"""Turn a recorded video into a session folder.

    uv run python -m memory.ingest.recording path/to/video.mov

Writes data/sessions/<start time>/ with:
    frames/        the sharpest frame from every few seconds; blurry and near-duplicate ones dropped
    audio.wav      mono 16 kHz, ready for speech-to-text (missing if the video has no sound)
    manifest.json  every kept frame with its time and fingerprint, and where it was recorded if known
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
from memory.ingest.blur import CANDIDATES_PER_SLOT, DEFAULT_MAX_BLUR, blur_scores, sharpest_per_slot
from memory.places import parse_location
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


def run(command: list[str], cwd: Path | None = None) -> bytes:
    """Run a command and return what it printed, or raise IngestError with its error text."""
    result = subprocess.run(command, capture_output=True, cwd=cwd)

    if result.returncode != 0:
        raise IngestError(f"{command[0]} failed: {result.stderr.decode(errors='replace').strip()}")

    return result.stdout


def probe(video: Path) -> tuple[datetime, float, bool, tuple[float, float] | None]:
    """Return when the recording started (local time), its length in seconds, whether it has sound, and
    (lat, lon) if the file says where it was recorded."""
    info = json.loads(
        run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)])
    )
    duration = float(info["format"].get("duration", 0))
    has_audio = any(stream["codec_type"] == "audio" for stream in info["streams"])

    tags = info["format"].get("tags", {})
    location = parse_location(tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location"))

    created = tags.get("creation_time")
    if created:
        started = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone()
    else:
        # No start time in the file: the file's modified time is when recording ended.
        ended = datetime.fromtimestamp(video.stat().st_mtime).astimezone()
        started = ended - timedelta(seconds=duration)

    return started, duration, has_audio, location


# Edge strength: each pixel compared with its four neighbours. Written once per colour plane for ffmpeg.
EDGES = "convolution='" + ":".join(["0 -1 0 -1 4 -1 0 -1 0"] * 4) + "'"


def read_averages(path: Path) -> list[float]:
    """Read the per-frame averages that ffmpeg's metadata filter printed to a file."""
    return [float(line.split("=")[1]) for line in path.read_text().splitlines() if "YAVG=" in line]


def extract_candidates(video: Path, frames_dir: Path, seconds_per_frame: float) -> tuple[list[int], list]:
    """Write CANDIDATES_PER_SLOT JPEGs for every `seconds_per_frame` seconds into frames_dir.

    Return each one's fingerprint and blur score. One ffmpeg pass writes the JPEGs, the tiny grey thumbnails
    and the two detail measurements, so all four always line up.
    """
    frames_dir.mkdir(parents=True)
    # Measured at up to 640 pixels wide. Small frames are left as they are, since enlarging one blurs it.
    measure = "scale='min(640,iw)':-2,format=gray"
    average = "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file="

    with tempfile.TemporaryDirectory() as scratch:
        graph = (
            f"[0:v]fps={CANDIDATES_PER_SLOT}/{seconds_per_frame},split=4[full][small][plain][soft];"
            f"[small]scale={THUMB_WIDTH}:{THUMB_HEIGHT},format=gray[thumb];"
            f"[plain]{measure},{EDGES},{average}detail.txt[detail];"
            f"[soft]{measure},gblur=sigma=1.5,{EDGES},{average}blurred.txt[blurred]"
        )

        # Run inside the scratch folder so the two measurement files can be named without a path.
        run([
            "ffmpeg", "-v", "error", "-i", str(video.resolve()),
            "-filter_complex", graph,
            "-map", "[full]", "-q:v", "3", str(frames_dir.resolve() / "%06d.jpg"),
            "-map", "[thumb]", "-f", "rawvideo", "thumbs.grey",
            "-map", "[detail]", "-f", "null", "-",
            "-map", "[blurred]", "-f", "null", "-",
        ], cwd=Path(scratch))

        grey = (Path(scratch) / "thumbs.grey").read_bytes()
        detail = read_averages(Path(scratch) / "detail.txt")
        blurred = read_averages(Path(scratch) / "blurred.txt")

    count = len(grey) // THUMB_BYTES
    written = len(list(frames_dir.glob("*.jpg")))
    if not (count == written == len(detail) == len(blurred)):
        raise IngestError(f"ffmpeg wrote {written} frames, {count} thumbnails and {len(detail)} measurements")

    fingerprints = [fingerprint(grey[i * THUMB_BYTES : (i + 1) * THUMB_BYTES]) for i in range(count)]

    return fingerprints, blur_scores(detail, blurred)


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

    parts = [f'<p>{html.escape(caption["summary"])}</p>', f'<p class="dim">Setting: {html.escape(caption["setting"])}</p>']

    for name, relation, anchor in caption["objects"]:
        parts.append(f'<p class="dim">{html.escape(f"{name}: {relation} {anchor}")}</p>')

    for text, surface in caption["texts"]:
        parts.append(f'<p class="dim">Text on {html.escape(surface)}: {html.escape(text)}</p>')

    return "".join(parts)


def write_page(
    session_dir: Path,
    manifest: dict,
    captions: dict[str, dict] | None = None,
    transcript: list[tuple] | None = None,
) -> None:
    """Write index.html: the kept frames on one page, plus captions and the transcript where they exist.

    captions is keyed by frame file name. transcript is a list of (offset seconds, speaker, text).
    """
    captions = captions or {}

    cards = "\n".join(
        f'<figure><img src="{html.escape(frame["file"])}" loading="lazy">'
        f'<figcaption>{clock(frame["offset_seconds"])}'
        f'{caption_html(captions.get(Path(frame["file"]).name))}</figcaption></figure>'
        for frame in manifest["frames"]
    )

    audio = '<audio controls src="audio.wav"></audio>' if manifest["audio"] else "<p>No sound in this recording.</p>"

    spoken = "\n".join(
        f'<p><span class="dim">{clock(offset)} {html.escape(speaker or "?")}</span> {html.escape(text)}</p>'
        for offset, speaker, text in transcript or []
    )

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
  .spoken {{ max-width: 70ch; margin: 12px 0 24px; }}
  .spoken p {{ margin: 4px 0; }}
  .spoken .dim {{ color: #999; font-size: 13px; margin-right: 6px; }}
</style>
<h1>Session {html.escape(manifest["id"])}</h1>
<p>From {html.escape(manifest["video"])}, {clock(manifest["duration_seconds"])} long.
Kept {manifest["frames_kept"]} of {manifest["frames_sampled"]} frames, one for every
{manifest["seconds_per_frame"]:g} seconds. {manifest.get("frames_blurry", 0)} were too blurry and the rest
looked the same as the frame before.</p>
{audio}
<div class="spoken">
{spoken}
</div>
<div class="grid">
{cards}
</div>
"""

    (session_dir / "index.html").write_text(page)


def refresh_page(conn, session_dir: Path) -> None:
    """Rewrite a session's index.html with whatever captions and transcript the database has for it."""
    manifest = json.loads((session_dir / "manifest.json").read_text())

    captions = {Path(frame["image_path"]).name: frame for frame in db.captioned_frames(conn, manifest["id"])}

    write_page(session_dir, manifest, captions, db.transcript(conn, manifest["id"]))


def ingest(
    video: Path,
    data_dir: Path = DATA_DIR,
    seconds_per_frame: float = DEFAULT_SECONDS_PER_FRAME,
    max_distance: int = DEFAULT_MAX_DISTANCE,
    max_blur: float = DEFAULT_MAX_BLUR,
    replace: bool = False,
) -> Path:
    """Turn a video into a session folder and return the folder's path."""
    if not video.is_file():
        raise IngestError(f"no such file: {video}")

    started, duration, has_audio, location = probe(video)
    session_id = started.strftime("%Y-%m-%dT%H-%M-%S")
    session_dir = data_dir / "sessions" / session_id
    if session_dir.exists():
        if not replace:
            raise IngestError(f"{session_dir} already exists. Pass --replace to build it again.")
        shutil.rmtree(session_dir)

    frames_dir = session_dir / "frames"
    fingerprints, blur = extract_candidates(video, frames_dir, seconds_per_frame)

    # One frame per slot: its sharpest. A slot with no sharp frame gives None and is dropped.
    slots = sharpest_per_slot(blur, CANDIDATES_PER_SLOT, max_blur)
    sharp = [index for index in slots if index is not None]

    # Then drop frames that look the same as the last one kept.
    kept = {sharp[position] for position in indexes_to_keep([fingerprints[index] for index in sharp], max_distance)}

    frames = []
    for index, path in enumerate(sorted(frames_dir.glob("*.jpg"))):
        if index not in kept:
            path.unlink()
            continue

        offset = index * seconds_per_frame / CANDIDATES_PER_SLOT
        frames.append({
            "file": f"frames/{path.name}",
            "offset_seconds": offset,
            "captured_at": (started + timedelta(seconds=offset)).isoformat(),
            "fingerprint": f"{fingerprints[index]:016x}",
            "blur": round(blur[index], 3),
        })

    if has_audio:
        extract_audio(video, session_dir / "audio.wav")

    manifest = {
        "id": session_id,
        "source": "recording",
        "video": video.name,
        "started_at": started.isoformat(),
        "duration_seconds": duration,
        "lat": location[0] if location else None,
        "lon": location[1] if location else None,
        "seconds_per_frame": seconds_per_frame,
        "frames_sampled": len(slots),
        "frames_blurry": len(slots) - len(sharp),
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
    parser.add_argument(
        "--max-blur", type=float, default=DEFAULT_MAX_BLUR,
        help="a moment is dropped when its sharpest frame scores above this; raise it to keep more",
    )
    parser.add_argument("--replace", action="store_true", help="rebuild the session if it already exists")
    args = parser.parse_args()

    try:
        session_dir = ingest(
            args.video,
            seconds_per_frame=args.seconds_per_frame,
            max_distance=args.max_distance,
            max_blur=args.max_blur,
            replace=args.replace,
        )
    except IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    manifest = json.loads((session_dir / "manifest.json").read_text())
    print(f"Kept {manifest['frames_kept']} of {manifest['frames_sampled']} frames"
          f" ({manifest['frames_blurry']} too blurry).")
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
