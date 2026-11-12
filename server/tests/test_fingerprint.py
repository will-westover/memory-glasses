import pytest

from memory.ingest.fingerprint import THUMB_BYTES, distance, fingerprint, indexes_to_keep


def test_fingerprint_marks_pixels_brighter_than_their_right_neighbour():
    falling = bytes(range(9, 0, -1)) * 8  # every pixel is brighter than the one to its right
    rising = bytes(range(1, 10)) * 8
    assert fingerprint(falling) == 2**64 - 1
    assert fingerprint(rising) == 0


def test_fingerprint_rejects_the_wrong_size():
    with pytest.raises(ValueError):
        fingerprint(bytes(THUMB_BYTES - 1))


def test_distance_counts_differing_bits():
    assert distance(0b1010, 0b1010) == 0
    assert distance(0b1010, 0b0101) == 4


def test_keeps_the_first_frame_and_each_new_scene():
    scene_a, scene_b = 0, 2**64 - 1
    assert indexes_to_keep([scene_a, scene_a, scene_b, scene_b, scene_a]) == [0, 2, 4]


def test_slow_change_is_kept_once_it_adds_up():
    # Each frame differs from the one before by 3 bits, under the limit of 6, but the change accumulates.
    frames = [0b0, 0b111, 0b111111, 0b111111111]
    assert indexes_to_keep(frames, max_distance=6) == [0, 3]


def test_nothing_in_nothing_out():
    assert indexes_to_keep([]) == []
