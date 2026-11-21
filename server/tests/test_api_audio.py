import wave

from memory.api.moments import audio_clip


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

    assert clip_seconds(audio_clip(tmp_path / "a.wav", 2, 5)) == 3


def test_clip_stops_at_the_end_of_the_recording(tmp_path):
    make_wav(tmp_path / "a.wav", 10)

    assert clip_seconds(audio_clip(tmp_path / "a.wav", 8, 30)) == 2
    assert clip_seconds(audio_clip(tmp_path / "a.wav", 20, 30)) == 0
