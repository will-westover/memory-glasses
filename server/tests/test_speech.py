import numpy as np
import pytest

from memory.ingest import speech
from memory.ingest.transcript import voice_letters

WINDOW = 0.1


def probabilities(*runs: tuple[float, float]) -> np.ndarray:
    values = []
    for probability, seconds in runs:
        values += [probability] * round(seconds / WINDOW)
    return np.array(values)


def test_speech_regions_start_and_end_on_the_windows_with_speech():
    regions = speech.speech_regions(probabilities((0.0, 1.0), (0.9, 2.0), (0.0, 1.0)), WINDOW)
    assert regions == [pytest.approx((1.0, 3.0))]


def test_short_pauses_join_and_long_pauses_split():
    joined = speech.speech_regions(probabilities((0.9, 1.0), (0.0, 0.4), (0.9, 1.0), (0.0, 2.0)), WINDOW)
    split = speech.speech_regions(probabilities((0.9, 1.0), (0.0, 1.5), (0.9, 1.0), (0.0, 2.0)), WINDOW)

    assert joined == [pytest.approx((0.0, 2.4))]
    assert split == [pytest.approx((0.0, 1.0)), pytest.approx((2.5, 3.5))]


def test_unsure_windows_do_not_end_speech():
    regions = speech.speech_regions(probabilities((0.9, 1.0), (0.4, 2.0), (0.9, 1.0), (0.0, 1.0)), WINDOW)
    assert regions == [pytest.approx((0.0, 4.0))]


def test_speech_at_the_end_is_kept_and_blips_are_dropped():
    regions = speech.speech_regions(probabilities((0.9, 0.1), (0.0, 2.0), (0.9, 1.0)), WINDOW)
    assert regions == [pytest.approx((2.1, 3.1))]


def test_long_regions_split_at_the_quietest_point_within_the_limit():
    values = probabilities((0.9, 6.0), (0.55, 0.1), (0.9, 6.0))
    parts = speech.split_long((0.0, 12.1), values, WINDOW, limit=10.0)

    assert parts == [pytest.approx((0.0, 6.0)), pytest.approx((6.0, 12.1))]
    assert all(end - start <= 10.0 for start, end in parts)


def test_very_long_regions_become_several_parts_that_cover_it_all():
    values = probabilities((0.9, 35.0))
    parts = speech.split_long((0.0, 35.0), values, WINDOW, limit=10.0)

    assert parts[0][0] == 0.0 and parts[-1][1] == 35.0
    assert all(a[1] == b[0] for a, b in zip(parts, parts[1:]))
    assert all(end - start <= 10.0 + 1e-9 for start, end in parts)


def test_padding_stops_at_the_ends_and_halfway_to_neighbours():
    widened = speech.padded([(0.1, 1.0), (1.2, 2.0), (3.0, 3.95)], duration=4.0, pad=0.3)
    assert widened == [(0.0, 1.1), (1.1, 2.3), (2.7, 4.0)]


def test_clip_cuts_by_seconds():
    samples = np.arange(speech.SAMPLE_RATE * 3)
    assert len(speech.clip(samples, 1.0, 2.5)) == speech.SAMPLE_RATE * 1.5


def test_alike_voices_share_a_letter():
    a, b = [1.0, 0.0], [0.0, 1.0]
    assert voice_letters([a, b, [0.9, 0.1], None, b]) == ["A", "B", "A", None, "B"]
