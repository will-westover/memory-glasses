import numpy as np

from server.voices import best_speaker


def unit(*values):
    vector = np.array(values, dtype=float)
    return vector / np.linalg.norm(vector)


def test_a_voice_like_one_person_gets_their_name():
    assert best_speaker(unit(1, 0.1), {1: unit(1, 0), 2: unit(0, 1)}) == 1


def test_a_voice_like_nobody_stays_unnamed():
    assert best_speaker(unit(-1, -1), {1: unit(1, 0), 2: unit(0, 1)}) is None


def test_a_voice_like_two_people_stays_unnamed():
    assert best_speaker(unit(1, 1), {1: unit(1, 0), 2: unit(0, 1)}) is None
