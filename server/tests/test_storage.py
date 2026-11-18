import boto3
import pytest
from moto import mock_aws

from memory import glasses, storage


@pytest.fixture
def r2(tmp_path, monkeypatch):
    with mock_aws():
        bucket = boto3.resource("s3", region_name="us-east-1").create_bucket(Bucket="memory")
        monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
        monkeypatch.setattr(storage, "enabled", lambda: True)
        monkeypatch.setattr(storage, "bucket", lambda: bucket)
        yield bucket


def test_a_mirrored_folder_comes_back_into_an_empty_data_folder(r2, tmp_path):
    folder = tmp_path / "moments" / "m1"
    (folder / "frames").mkdir(parents=True)
    (folder / "frames" / "00001000.jpg").write_bytes(b"photo")
    (folder / "video.mov").write_bytes(b"video")
    storage.mirror(folder)

    (folder / "frames" / "00001000.jpg").unlink()
    (folder / "video.mov").unlink()
    storage.fetch_folder(folder)

    assert (folder / "frames" / "00001000.jpg").read_bytes() == b"photo"
    assert not (folder / "video.mov").exists()
    assert storage.local(folder / "video.mov").read_bytes() == b"video"
    assert storage.local(folder / "missing.json") is None


def test_mirroring_drops_files_the_folder_no_longer_has(r2, tmp_path):
    folder = tmp_path / "moments" / "m1"
    folder.mkdir(parents=True)
    (folder / "a.jpg").write_bytes(b"a")
    (folder / "b.jpg").write_bytes(b"b")
    storage.mirror(folder)

    (folder / "b.jpg").unlink()
    storage.mirror(folder)

    assert sorted(storage.listing("moments/")) == ["moments/m1/a.jpg"]


def test_recordings_are_grouped_and_a_video_marks_one_done():
    found = glasses.summarise({
        "live/000000000.jpg": 10.0, "live/000002000.jpg": 12.0, "live/started": 10.0,
        "done/000000000.jpg": 5.0, "done/video.mp4": 70.0,
        "empty/started": 1.0,
    })

    assert found == {"live": {"photos": 2, "last": 12.0, "done": False},
                     "done": {"photos": 1, "last": 5.0, "done": True}}
