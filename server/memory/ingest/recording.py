"""Turn a recorded video into a session folder of kept frames, audio and a page to check them.

    uv run python -m memory.ingest.recording path/to/video.mov
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
from memory.config import DATA_DIR, FRAMES_PER_MINUTE
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

MAX_FRAMES_PER_MINUTE = 60


class IngestError(Exception):
    """The recording could not be turned into a session."""


def run(command: list[str], cwd: Path | None = None) -> bytes:
    """Run a command and return what it printed."""
    result = subprocess.run(command, capture_output=True, cwd=cwd)

    if result.returncode != 0:
        raise IngestError(f"{command[0]} failed: {result.stderr.decode(errors='replace').strip()}")

    return result.stdout


def video_info(video: Path) -> dict:
    """Return what ffprobe reports about a video."""
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(video)]))


def duration(info: dict) -> float:
    """Return the video's length in seconds."""
    return float(info["format"].get("duration", 0))


def has_sound(info: dict) -> bool:
    """Return whether the video has an audio track."""
    return any(stream["codec_type"] == "audio" for stream in info["streams"])


def location(info: dict) -> tuple[float, float] | None:
    """Return (lat, lon) if the video says where it was recorded."""
    tags = info["format"].get("tags", {})

    return parse_location(tags.get("com.apple.quicktime.location.ISO6709") or tags.get("location"))


def start_time(info: dict, video: Path) -> datetime:
    """Return when the recording started, from its creation tag or the file's date."""
    created = info["format"].get("tags", {}).get("creation_time")

    if created:
        return datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone()

    ended = datetime.fromtimestamp(video.stat().st_mtime).astimezone()

    return ended - timedelta(seconds=duration(info))


EDGES = "convolution='" + ":".join(["0 -1 0 -1 4 -1 0 -1 0"] * 4) + "'"


def read_averages(path: Path) -> list[float]:
    """Read the per-frame averages ffmpeg printed to a file."""
    return [float(line.split("=")[1]) for line in path.read_text().splitlines() if "YAVG=" in line]


def filter_graph(seconds_per_frame: float) -> str:
    """Return the ffmpeg filters that produce the JPEGs, thumbnails and two detail measurements."""
    measure = "scale='min(640,iw)':-2,format=gray"
    average = "signalstats,metadata=print:key=lavfi.signalstats.YAVG:file="

    return (
        f"[0:v]fps={CANDIDATES_PER_SLOT}/{seconds_per_frame},split=4[full][small][plain][soft];"
        f"[small]scale={THUMB_WIDTH}:{THUMB_HEIGHT},format=gray[thumb];"
        f"[plain]{measure},{EDGES},{average}detail.txt[detail];"
        f"[soft]{measure},gblur=sigma=1.5,{EDGES},{average}blurred.txt[blurred]"
    )


def sample_video(video: Path, frames_dir: Path, seconds_per_frame: float) -> tuple[bytes, list[float], list[float]]:
    """Write the candidate JPEGs and return the thumbnails and the two detail measurements."""
    with tempfile.TemporaryDirectory() as scratch:
        run([
            "ffmpeg", "-v", "error", "-i", str(video.resolve()),
            "-filter_complex", filter_graph(seconds_per_frame),
            "-map", "[full]", "-q:v", "3", str(frames_dir.resolve() / "%06d.jpg"),
            "-map", "[thumb]", "-f", "rawvideo", "thumbs.grey",
            "-map", "[detail]", "-f", "null", "-",
            "-map", "[blurred]", "-f", "null", "-",
        ], cwd=Path(scratch))

        grey = (Path(scratch) / "thumbs.grey").read_bytes()
        detail = read_averages(Path(scratch) / "detail.txt")
        blurred = read_averages(Path(scratch) / "blurred.txt")

    return grey, detail, blurred


def extract_candidates(video: Path, frames_dir: Path, seconds_per_frame: float) -> tuple[list[int], list]:
    """Write the candidate JPEGs and return each one's fingerprint and blur score."""
    grey, detail, blurred = sample_video(video, frames_dir, seconds_per_frame)

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
    """Return the HTML shown under a frame for its caption."""
    if not caption:
        return ""

    parts = [f'<p>{html.escape(caption["summary"])}</p>']

    for name, location in caption["objects"]:
        parts.append(f'<p class="dim">{html.escape(f"{name}: {location}")}</p>')

    for text, surface in caption["texts"]:
        parts.append(f'<p class="dim">Text on {html.escape(surface)}: {html.escape(text)}</p>')

    return "".join(parts)


def write_page(
    session_dir: Path,
    manifest: dict,
    captions: dict[str, dict] | None = None,
    transcript: list[tuple] | None = None,
) -> None:
    """Write index.html: the kept frames, with captions and the transcript where they exist."""
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
Kept {manifest["frames_kept"]} of {manifest["frames_sampled"]} frames, sampled at
{manifest["frames_per_minute"]:g} a minute. {manifest.get("frames_blurry", 0)} were too blurry and the rest
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
    """Rewrite a session's index.html from the database."""
    manifest = json.loads((session_dir / "manifest.json").read_text())

    captions = {Path(frame["image_path"]).name: frame for frame in db.captioned_frames(conn, manifest["id"])}

    write_page(session_dir, manifest, captions, db.transcript(conn, manifest["id"]))


def new_session_dir(data_dir: Path, session_id: str, replace: bool) -> Path:
    """Return an empty folder for the session, removing an earlier one if replace is set."""
    session_dir = data_dir / "sessions" / session_id

    if session_dir.exists():
        if not replace:
            raise IngestError(f"{session_dir} already exists. Pass --replace to build it again.")

        shutil.rmtree(session_dir)

    (session_dir / "frames").mkdir(parents=True)

    return session_dir


def choose_frames(fingerprints: list[int], blur: list, max_distance: int, max_blur: float) -> tuple[int, int, set[int]]:
    """Return how many slots there were, how many were sharp, and which frames to keep."""
    slots = sharpest_per_slot(blur, CANDIDATES_PER_SLOT, max_blur)
    sharp = [index for index in slots if index is not None]

    different = indexes_to_keep([fingerprints[index] for index in sharp], max_distance)

    return len(slots), len(sharp), {sharp[position] for position in different}


def keep_frames(frames_dir: Path, kept: set[int], fingerprints: list[int], blur: list,
                started: datetime, seconds_per_frame: float) -> list[dict]:
    """Delete the frames that were not chosen and return a manifest entry for each one kept."""
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

    return frames


def ingest(
    video: Path,
    data_dir: Path = DATA_DIR,
    frames_per_minute: float = FRAMES_PER_MINUTE,
    max_distance: int = DEFAULT_MAX_DISTANCE,
    max_blur: float = DEFAULT_MAX_BLUR,
    replace: bool = False,
) -> Path:
    """Turn a video into a session folder and return the folder's path."""
    if not video.is_file():
        raise IngestError(f"no such file: {video}")

    if not 0 <= frames_per_minute <= MAX_FRAMES_PER_MINUTE:
        raise IngestError(f"frames per minute must be between 0 and {MAX_FRAMES_PER_MINUTE}")

    info = video_info(video)
    started = start_time(info, video)
    place = location(info)

    session_id = started.strftime("%Y-%m-%dT%H-%M-%S")
    session_dir = new_session_dir(data_dir, session_id, replace)
    frames_dir = session_dir / "frames"

    seconds_per_frame = 60 / frames_per_minute if frames_per_minute else 0.0
    fingerprints, blur = extract_candidates(video, frames_dir, seconds_per_frame) if frames_per_minute else ([], [])

    sampled, sharp, kept = choose_frames(fingerprints, blur, max_distance, max_blur)
    frames = keep_frames(frames_dir, kept, fingerprints, blur, started, seconds_per_frame)

    if has_sound(info):
        extract_audio(video, session_dir / "audio.wav")

    manifest = {
        "id": session_id,
        "source": "recording",
        "video": video.name,
        "started_at": started.isoformat(),
        "duration_seconds": duration(info),
        "lat": place[0] if place else None,
        "lon": place[1] if place else None,
        "frames_per_minute": frames_per_minute,
        "frames_sampled": sampled,
        "frames_blurry": sampled - sharp,
        "frames_kept": len(frames),
        "audio": "audio.wav" if has_sound(info) else None,
        "frames": frames,
    }

    (session_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    write_page(session_dir, manifest)

    return session_dir


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Turn a recorded video into a session folder.")
    parser.add_argument("video", type=Path)
    parser.add_argument(
        "--frames-per-minute", type=float, default=FRAMES_PER_MINUTE,
        help=f"how many pictures to take each minute, 0 to {MAX_FRAMES_PER_MINUTE}; 0 keeps sound only",
    )
    parser.add_argument(
        "--max-distance", type=int, default=DEFAULT_MAX_DISTANCE,
        help="frames differing in this many fingerprint bits or fewer are dropped (0 to 64)",
    )
    parser.add_argument(
        "--max-blur", type=float, default=DEFAULT_MAX_BLUR,
        help="a moment is dropped when its sharpest frame scores above this; raise it to keep more",
    )
    parser.add_argument("--replace", action="store_true", help="rebuild the session if it already exists")

    return parser.parse_args()


def print_report(session_dir: Path) -> None:
    manifest = json.loads((session_dir / "manifest.json").read_text())

    print(f"Kept {manifest['frames_kept']} of {manifest['frames_sampled']} frames"
          f" ({manifest['frames_blurry']} too blurry).")
    print(f"Sound: {'audio.wav' if manifest['audio'] else 'none'}")
    print(f"Look at: {session_dir / 'index.html'}")


def save_to_database(session_dir: Path) -> None:
    try:
        with db.connect() as conn:
            db.save_session(conn, session_dir)

        print("Saved to the database.")
    except psycopg.OperationalError:
        print("Not saved: the database isn't running. Start it with: docker compose up -d --wait")


def main() -> int:
    args = parse_arguments()

    try:
        session_dir = ingest(
            args.video,
            frames_per_minute=args.frames_per_minute,
            max_distance=args.max_distance,
            max_blur=args.max_blur,
            replace=args.replace,
        )
    except IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print_report(session_dir)
    save_to_database(session_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
