
from memory import glasses, storage


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


def test_a_kept_video_is_copied_inside_r2_so_mirroring_sends_nothing(r2, tmp_path):
    upload = tmp_path / "uploads" / "job" / "video.mov"
    upload.parent.mkdir(parents=True)
    upload.write_bytes(b"video")
    storage.save(upload)

    folder = tmp_path / "moments" / "m1"
    folder.mkdir(parents=True)
    (folder / "video.mov").write_bytes(b"video")
    storage.copy(upload, folder / "video.mov")

    sent = []
    original = r2.upload_file
    r2.upload_file = lambda *args, **kwargs: sent.append(args) or original(*args, **kwargs)
    storage.mirror(folder)

    assert sent == []
    assert r2.Object("moments/m1/video.mov").get()["Body"].read() == b"video"


def test_a_frame_the_glasses_put_in_r2_takes_its_place_in_the_recording(r2, tmp_path, monkeypatch):
    monkeypatch.setattr(glasses, "GLASSES_DIR", tmp_path / "glasses")
    monkeypatch.setattr(glasses, "place", lambda conn, recording, taken: tmp_path / "glasses" / recording / "000002500.jpg")
    r2.put_object(Key="glasses/rec1/" + "a" * 32 + ".pending", Body=b"\xff\xd8 first")

    path = glasses.arrived(None, "rec1", "a" * 32)

    assert path.name == "000002500.jpg"
    assert sorted(item.key for item in r2.objects.all()) == ["glasses/rec1/000002500.jpg"]
    assert glasses.arrived(None, "rec1", "c" * 32) is None
