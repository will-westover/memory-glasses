"""Turn a recorded video into a moment folder of kept frames, audio and a page to check them."""

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
from memory.config import DATA_DIR, FRAMES_PER_MINUTE, clock
from memory.ingest.blur import CANDIDATES_PER_SLOT, DEFAULT_MAX_BLUR, blur_scores, sharpest_per_slot
from memory.ingest.poses import load_poses, nearest_pose
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
    """The recording could not be turned into a moment."""


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
    tags = info["format"].get("tags", {})
    created = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")

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
    moment_dir: Path,
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
<title>Moment {html.escape(manifest["id"])}</title>
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
<h1>Moment {html.escape(manifest["id"])}</h1>
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

    (moment_dir / "index.html").write_text(page)


def refresh_page(conn, moment_dir: Path) -> None:
    """Rewrite a moment's index.html from the database."""
    manifest = json.loads((moment_dir / "manifest.json").read_text())

    captions = {Path(frame["image_path"]).name: frame for frame in db.captioned_frames(conn, manifest["id"])}

    write_page(moment_dir, manifest, captions, db.transcript(conn, manifest["id"]))


def keep_file(source: Path, target: Path) -> None:
    """Put a copy of a file at target, as a hard link when both are on the same disk."""
    try:
        target.hardlink_to(source)
    except OSError:
        shutil.copy2(source, target)


PLAYABLE = {".mov", ".mp4", ".m4v"}


def keep_video(video: Path, folder: Path) -> str:
    """Keep the original video in the folder, converted to MP4 if phones can't play its format. Return its name."""
    if video.suffix.lower() in PLAYABLE:
        name = f"video{video.suffix.lower()}"
        keep_file(video, folder / name)
        return name

    run(["ffmpeg", "-v", "error", "-i", str(video), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-c:a", "aac", "-movflags", "+faststart", str(folder / "video.mp4")])

    return "video.mp4"


def build_moment(video: Path, info: dict, build_dir: Path, moment_id: str, frames_per_minute: float,
                 max_distance: int, max_blur: float, extras: dict[str, Path], reuse: list[dict]) -> None:
    """Fill a folder with the moment's video, kept frames, audio, extra files, manifest and page."""
    started = start_time(info, video)
    place = location(info)
    frames_dir = build_dir / "frames"
    frames_dir.mkdir(parents=True)

    seconds_per_frame = 60 / frames_per_minute if frames_per_minute else 0.0
    fingerprints, blur = extract_candidates(video, frames_dir, seconds_per_frame) if frames_per_minute else ([], [])
    candidates = sorted(frames_dir.glob("*.jpg"))

    sampled, usable, picks = choose_frames(fingerprints, blur, seconds_per_frame, max_distance, max_blur, candidates,
                                           reuse)
    frames = keep_frames(frames_dir, picks, candidates, started)
    poses = load_poses(extras["poses.json"]) if "poses.json" in extras else []

    for frame in frames:
        pose = nearest_pose(poses, frame["offset_seconds"])
        frame["position"] = pose[1:] if pose else None

    if has_sound(info):
        extract_audio(video, build_dir / "audio.wav")

    video_file = keep_video(video, build_dir)

    for name, source in extras.items():
        keep_file(source, build_dir / name)

    manifest = {
        "id": moment_id,
        "source": "recording",
        "video": video.name,
        "video_file": video_file,
        "video_bytes": video.stat().st_size,
        "extras": sorted(extras),
        "started_at": started.isoformat(),
        "duration_seconds": duration(info),
        "lat": place[0] if place else None,
        "lon": place[1] if place else None,
        "frames_per_minute": frames_per_minute,
        "frames_sampled": sampled,
        "frames_blurry": sampled - usable,
        "frames_kept": len(frames),
        "audio": "audio.wav" if has_sound(info) else None,
        "frames": frames,
    }

    (build_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    write_page(build_dir, manifest)


def frame_offset(index: int, seconds_per_frame: float) -> float:
    """Return the time in the video of a sampled candidate frame."""
    return index * seconds_per_frame / CANDIDATES_PER_SLOT


def frame_name(offset: float) -> str:
    """Return a frame's file name from its time in the video, in milliseconds, so it stays the same at any rate."""
    return f"{round(offset * 1000):08d}.jpg"


def slot_picks(slots: list[int | None], fingerprints: list[int], blur: list, seconds_per_frame: float,
               candidates: list[Path], reuse: list[dict]) -> list[dict]:
    """Return one frame per slot: a frame already kept inside the slot if there is one, else the sharpest sample."""
    picks = []

    for slot, index in enumerate(slots):
        start, end = slot * seconds_per_frame, (slot + 1) * seconds_per_frame
        existing = next((frame for frame in reuse if start <= frame["offset_seconds"] < end), None)

        if existing:
            picks.append({"offset": existing["offset_seconds"], "source": existing["path"],
                          "fingerprint": int(existing["fingerprint"], 16), "blur": existing.get("blur")})
        elif index is not None:
            picks.append({"offset": frame_offset(index, seconds_per_frame), "source": candidates[index],
                          "fingerprint": fingerprints[index], "blur": blur[index]})

    return picks


def choose_frames(fingerprints: list[int], blur: list, seconds_per_frame: float, max_distance: int, max_blur: float,
                  candidates: list[Path], reuse: list[dict]) -> tuple[int, int, list[dict]]:
    """Return how many slots there were, how many had a usable frame, and the frames to keep."""
    slots = sharpest_per_slot(blur, CANDIDATES_PER_SLOT, max_blur)
    picks = slot_picks(slots, fingerprints, blur, seconds_per_frame, candidates, reuse)

    different = indexes_to_keep([pick["fingerprint"] for pick in picks], max_distance)

    return len(slots), len(picks), [picks[position] for position in different]


def keep_frames(frames_dir: Path, picks: list[dict], candidates: list[Path], started: datetime) -> list[dict]:
    """Put each chosen frame in place under its time-based name, delete the other samples, and describe them."""
    frames = []

    for pick in picks:
        target = frames_dir / frame_name(pick["offset"])

        if pick["source"].parent == frames_dir:
            pick["source"].rename(target)
        else:
            shutil.copy2(pick["source"], target)

        frames.append({
            "file": f"frames/{target.name}",
            "offset_seconds": pick["offset"],
            "captured_at": (started + timedelta(seconds=pick["offset"])).isoformat(),
            "fingerprint": f"{pick['fingerprint']:016x}",
            "blur": round(pick["blur"], 3) if pick["blur"] is not None else None,
        })

    for candidate in candidates:
        candidate.unlink(missing_ok=True)

    return frames


def kept_frames(moment_dir: Path) -> list[dict]:
    """Return a moment's current frames with their file paths, to reuse when it's rebuilt at another rate."""
    manifest = moment_dir / "manifest.json"

    if not manifest.is_file():
        return []

    return [{**frame, "path": moment_dir / frame["file"]} for frame in json.loads(manifest.read_text())["frames"]
            if (moment_dir / frame["file"]).is_file()]


def planned_frames(video: Path, frames_per_minute: float, reuse: list[dict] | None = None,
                   max_distance: int = DEFAULT_MAX_DISTANCE, max_blur: float = DEFAULT_MAX_BLUR) -> list[dict]:
    """Return the frames that would be kept at a rate, each saying whether it's reused, without keeping anything."""
    if not frames_per_minute:
        return []

    seconds_per_frame = 60 / frames_per_minute

    with tempfile.TemporaryDirectory() as scratch:
        frames_dir = Path(scratch) / "frames"
        frames_dir.mkdir()
        fingerprints, blur = extract_candidates(video, frames_dir, seconds_per_frame)
        candidates = sorted(frames_dir.glob("*.jpg"))

        _, _, picks = choose_frames(fingerprints, blur, seconds_per_frame, max_distance, max_blur, candidates,
                                    reuse or [])

    return [{"name": frame_name(pick["offset"]), "reused": pick["source"].parent != frames_dir} for pick in picks]


def ingest(
    video: Path,
    data_dir: Path = DATA_DIR,
    frames_per_minute: float = FRAMES_PER_MINUTE,
    max_distance: int = DEFAULT_MAX_DISTANCE,
    max_blur: float = DEFAULT_MAX_BLUR,
    replace: bool = False,
    extras: dict[str, Path] | None = None,
    reuse_frames: bool = False,
) -> Path:
    """Turn a video into a moment folder and return the folder's path. extras are files kept alongside it."""
    if not video.is_file():
        raise IngestError(f"no such file: {video}")

    if not 0 <= frames_per_minute <= MAX_FRAMES_PER_MINUTE:
        raise IngestError(f"frames per minute must be between 0 and {MAX_FRAMES_PER_MINUTE}")

    info = video_info(video)
    moment_id = start_time(info, video).strftime("%Y-%m-%dT%H-%M-%S")
    moment_dir = data_dir / "moments" / moment_id
    build_dir = data_dir / "moments" / f"{moment_id}.building"

    if moment_dir.exists() and not replace:
        raise IngestError(f"{moment_dir} already exists. Pass --replace to build it again.")

    shutil.rmtree(build_dir, ignore_errors=True)
    reuse = kept_frames(moment_dir) if reuse_frames else []

    try:
        build_moment(video, info, build_dir, moment_id, frames_per_minute, max_distance, max_blur, extras or {},
                     reuse)
    except Exception:
        shutil.rmtree(build_dir, ignore_errors=True)
        raise

    shutil.rmtree(moment_dir, ignore_errors=True)
    build_dir.rename(moment_dir)

    return moment_dir


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Turn a recorded video into a moment folder.")
    parser.add_argument("video", type=Path)
    parser.add_argument(
        "--frames-per-minute", type=float,
        help=f"how many pictures to take each minute, 0 to {MAX_FRAMES_PER_MINUTE}; defaults to the saved setting",
    )
    parser.add_argument(
        "--max-distance", type=int, default=DEFAULT_MAX_DISTANCE,
        help="frames differing in this many fingerprint bits or fewer are dropped (0 to 64)",
    )
    parser.add_argument(
        "--max-blur", type=float, default=DEFAULT_MAX_BLUR,
        help="a slot is dropped when its sharpest frame scores above this; raise it to keep more",
    )
    parser.add_argument("--replace", action="store_true", help="rebuild the moment if it already exists")

    return parser.parse_args()


def print_report(moment_dir: Path) -> None:
    manifest = json.loads((moment_dir / "manifest.json").read_text())

    print(f"Kept {manifest['frames_kept']} of {manifest['frames_sampled']} frames"
          f" ({manifest['frames_blurry']} too blurry).")
    print(f"Sound: {'audio.wav' if manifest['audio'] else 'none'}")
    print(f"Look at: {moment_dir / 'index.html'}")


def save_to_database(moment_dir: Path) -> None:
    try:
        with db.connect() as conn:
            saved = db.save_moment(conn, moment_dir)

        print(f"Saved to the database. {saved['captions_kept']} captions kept, {saved['captions_lost']} lost.")

        if saved["changed"]:
            print("The frames changed, so run the captions and facts steps again.")
    except psycopg.OperationalError:
        print("Not saved: the database isn't running. Start it with: docker compose up -d --wait")


def saved_rate() -> float:
    """Return the frames per minute saved in the database, or the default if it can't be read."""
    try:
        with db.connect() as conn:
            return float(db.setting(conn, "frames_per_minute", FRAMES_PER_MINUTE))
    except psycopg.OperationalError:
        return FRAMES_PER_MINUTE


def main() -> int:
    args = parse_arguments()

    try:
        moment_dir = ingest(
            args.video,
            frames_per_minute=saved_rate() if args.frames_per_minute is None else args.frames_per_minute,
            max_distance=args.max_distance,
            max_blur=args.max_blur,
            replace=args.replace,
        )
    except IngestError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print_report(moment_dir)
    save_to_database(moment_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
