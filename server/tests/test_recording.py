import json
import subprocess
from pathlib import Path

from memory.ingest.recording import ingest


def make_video(path: Path) -> None:
    """Make a 16-second test video with sound and two different scenes."""
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

    moment_dir = ingest(video, data_dir=tmp_path / "data")

    manifest = json.loads((moment_dir / "manifest.json").read_text())
    assert manifest["frames_sampled"] == 8
    assert manifest["frames_kept"] == 2
    assert manifest["frames_blurry"] == 0

    first, second = (frame["offset_seconds"] for frame in manifest["frames"])
    assert 0 <= first < 2 and 8 <= second < 10
    assert (manifest["lat"], manifest["lon"]) == (40.2518, -111.6493)

    on_disk = sorted(path.name for path in (moment_dir / "frames").glob("*.jpg"))
    assert on_disk == [Path(frame["file"]).name for frame in manifest["frames"]]
    assert (moment_dir / "audio.wav").stat().st_size > 0
    assert (moment_dir / "index.html").exists()


def test_frames_are_named_by_their_time_so_a_new_rate_finds_the_same_names():
    from memory.ingest.recording import frame_name, frame_offset

    at_30 = frame_name(frame_offset(4, 2.0))
    at_60 = frame_name(frame_offset(8, 1.0))

    assert at_30 == at_60 == "00002000.jpg"


def test_a_frame_already_kept_inside_a_slot_is_reused():
    from pathlib import Path

    from memory.ingest.recording import slot_picks

    reuse = [{"offset_seconds": 0.5, "path": Path("/old/00000500.jpg"), "fingerprint": "00000000000000ff", "blur": 0.3}]
    candidates = [Path(f"/new/{index:06d}.jpg") for index in range(8)]

    picks = slot_picks([1, 6], list(range(8)), [0.3] * 8, 1.0, candidates, reuse)

    assert [pick["offset"] for pick in picks] == [0.5, 1.5]
    assert picks[0]["source"] == Path("/old/00000500.jpg")
    assert picks[1]["source"] == Path("/new/000006.jpg")


def test_extra_files_and_the_source_are_kept(tmp_path):
    video = tmp_path / "walk.mp4"
    make_video(video)
    poses = tmp_path / "poses.json"
    poses.write_text(json.dumps({"poses": [[t / 10, t / 10, 1.5, 0.0, 0.0] for t in range(170)]}))

    moment_dir = ingest(video, data_dir=tmp_path / "data", extras={"poses.json": poses}, source="iphone")

    manifest = json.loads((moment_dir / "manifest.json").read_text())
    assert manifest["source"] == "iphone"
    assert manifest["extras"] == ["poses.json"]
    assert (moment_dir / "poses.json").is_file()
    assert all(frame["position"] for frame in manifest["frames"])
