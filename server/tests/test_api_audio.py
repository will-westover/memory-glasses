import wave

from server.api.moments import audio_clip
from server.shared.storage import read_range


def reader(path):
    return lambda first, last: read_range(path, first, last)


def make_wav(path, seconds, rate=1000):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(bytes(2 * rate * seconds))


def clip_seconds(data):
    import io

    with wave.open(io.BytesIO(data)) as clip:
        return clip.getnframes() / clip.getframerate()


def test_clip_covers_the_requested_stretch(tmp_path):
    make_wav(tmp_path / "a.wav", 10)

    assert clip_seconds(audio_clip(reader(tmp_path / "a.wav"), 2, 5)) == 3


def test_clip_stops_at_the_end_of_the_recording(tmp_path):
    make_wav(tmp_path / "a.wav", 10)

    assert clip_seconds(audio_clip(reader(tmp_path / "a.wav"), 8, 30)) == 2
    assert clip_seconds(audio_clip(reader(tmp_path / "a.wav"), 20, 30)) == 0


def test_a_chapter_loses_the_room_and_place_the_search_index_adds():
    from server.api.moments import without_place

    assert without_place("They ate edamame. Room: Dining Area. At Orem.") == "They ate edamame."
    assert without_place("At noon they left.") == "At noon they left."
