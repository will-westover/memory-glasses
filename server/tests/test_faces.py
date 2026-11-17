import numpy as np

from memory.ingest.faces import best_match


def unit(*values):
    vector = np.array(values, dtype=float)
    return vector / np.linalg.norm(vector)


def test_a_similar_face_joins_the_closest_person():
    people = {1: unit(1, 0, 0) * 5, 2: unit(0, 1, 0)}

    assert best_match(unit(0.9, 0.2, 0), people) == 1


def test_a_face_unlike_anyone_starts_nobody():
    people = {1: unit(1, 0, 0)}

    assert best_match(unit(0, 0, 1), people) is None


def test_an_empty_list_matches_nobody():
    assert best_match(unit(1, 0, 0), {}) is None
