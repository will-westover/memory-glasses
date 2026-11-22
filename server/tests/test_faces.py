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


def test_a_face_whose_eyes_reach_the_edge_is_cut_off():
    from memory.ingest.faces import cut_off

    whole = [(0.4, 0.3), (0.5, 0.3), (0.45, 0.35), (0.42, 0.4), (0.48, 0.4)]

    assert not cut_off(whole)
    assert cut_off([(0.4, 0.005), *whole[1:]])
    assert cut_off([*whole[:4], (1.004, 0.4)])


def test_a_flat_face_is_not_sharp():
    import numpy as np

    from memory.ingest.faces import sharpness

    flat = np.full((200, 200, 3), 128, np.uint8)
    noisy = np.random.default_rng(0).integers(0, 255, (200, 200, 3), dtype=np.uint8)

    assert sharpness(flat, (0.1, 0.1, 0.5, 0.5)) == 0
    assert sharpness(noisy, (0.1, 0.1, 0.5, 0.5)) > 22
