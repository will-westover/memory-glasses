import os

from memory import glasses


def test_each_frame_lasts_until_the_next():
    assert glasses.frame_durations(["000000000.jpg", "000002000.jpg", "000005500.jpg"]) == [2.0, 3.5, 3.5]


def test_a_single_frame_lasts_a_second():
    assert glasses.frame_durations(["000000000.jpg"]) == [1.0]


def test_frames_are_named_by_time_since_the_first(tmp_path, monkeypatch):
    monkeypatch.setattr(glasses, "GLASSES_DIR", tmp_path)

    first = glasses.save_frame("abc", b"\xff\xd8one", now=100.0)
    second = glasses.save_frame("abc", b"\xff\xd8two", now=102.25)

    assert (first.name, second.name) == ("000000000.jpg", "000002250.jpg")


def test_only_recordings_quiet_for_a_while_are_finished(tmp_path, monkeypatch):
    monkeypatch.setattr(glasses, "GLASSES_DIR", tmp_path)
    glasses.save_frame("old", b"\xff\xd8", now=0.0)
    glasses.save_frame("new", b"\xff\xd8", now=0.0)

    os.utime(tmp_path / "old" / "000000000.jpg", (0, 0))
    os.utime(tmp_path / "new" / "000000000.jpg", (990, 990))

    assert [folder.name for folder in glasses.finished(now=1000.0)] == ["old"]
