import json
import subprocess
from pathlib import Path

from memory.ingest.recording import ingest


def make_video(path: Path) -> None:
    """Make a 16-second test video with sound: 8 seconds of colour bars, then 8 seconds of a different pattern."""
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "smptebars=d=8:s=320x240:r=10",
            "-f", "lavfi", "-i", "testsrc=d=8:s=320x240:r=10",
            "-f", "lavfi", "-i", "sine=f=440:d=16",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a",
            "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-metadata", "location=+40.2518-111.6493/", str(path),
        ],
        check=True,
    )


def test_two_scenes_become_two_frames(tmp_path):
    video = tmp_path / "walk.mp4"
    make_video(video)

    session_dir = ingest(video, data_dir=tmp_path / "data")

    manifest = json.loads((session_dir / "manifest.json").read_text())
    assert manifest["frames_sampled"] == 8
    assert manifest["frames_kept"] == 2
    assert manifest["frames_blurry"] == 0

    first, second = (frame["offset_seconds"] for frame in manifest["frames"])
    assert 0 <= first < 2 and 8 <= second < 10
    assert (manifest["lat"], manifest["lon"]) == (40.2518, -111.6493)

    on_disk = sorted(path.name for path in (session_dir / "frames").glob("*.jpg"))
    assert on_disk == [Path(frame["file"]).name for frame in manifest["frames"]]
    assert (session_dir / "audio.wav").stat().st_size > 0
    assert (session_dir / "index.html").exists()
