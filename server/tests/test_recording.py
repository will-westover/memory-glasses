import json
import subprocess
from pathlib import Path

import pytest

from memory.ingest.recording import IngestError, ingest


def make_video(path: Path, with_sound: bool = True) -> None:
    """Make a 16-second test video: 8 seconds of colour bars, then 8 seconds of a different pattern."""
    command = [
        "ffmpeg", "-v", "error",
        "-f", "lavfi", "-i", "smptebars=d=8:s=320x240:r=10",
        "-f", "lavfi", "-i", "testsrc=d=8:s=320x240:r=10",
    ]
    if with_sound:
        command += ["-f", "lavfi", "-i", "sine=f=440:d=16"]
    command += ["-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]"]
    if with_sound:
        command += ["-map", "2:a"]
    command += ["-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True)


def test_two_scenes_become_two_frames(tmp_path):
    video = tmp_path / "walk.mp4"
    make_video(video)

    session_dir = ingest(video, data_dir=tmp_path / "data")

    manifest = json.loads((session_dir / "manifest.json").read_text())
    assert manifest["frames_sampled"] == 8
    assert manifest["frames_kept"] == 2
    assert [frame["offset_seconds"] for frame in manifest["frames"]] == [0.0, 8.0]

    on_disk = sorted(path.name for path in (session_dir / "frames").glob("*.jpg"))
    assert on_disk == [Path(frame["file"]).name for frame in manifest["frames"]]
    assert (session_dir / "audio.wav").stat().st_size > 0
    assert (session_dir / "index.html").exists()


def test_video_without_sound_has_no_audio_file(tmp_path):
    video = tmp_path / "silent.mp4"
    make_video(video, with_sound=False)

    session_dir = ingest(video, data_dir=tmp_path / "data")

    manifest = json.loads((session_dir / "manifest.json").read_text())
    assert manifest["audio"] is None
    assert not (session_dir / "audio.wav").exists()


def test_second_run_needs_replace(tmp_path):
    video = tmp_path / "walk.mp4"
    make_video(video)
    ingest(video, data_dir=tmp_path / "data")

    with pytest.raises(IngestError, match="already exists"):
        ingest(video, data_dir=tmp_path / "data")
    ingest(video, data_dir=tmp_path / "data", replace=True)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(IngestError, match="no such file"):
        ingest(tmp_path / "nope.mp4", data_dir=tmp_path / "data")
