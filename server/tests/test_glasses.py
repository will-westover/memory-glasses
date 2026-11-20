from memory import glasses


def test_each_frame_lasts_until_the_next():
    assert glasses.frame_durations(["000000000.jpg", "000002000.jpg", "000005500.jpg"]) == [2.0, 3.5, 3.5]


def test_a_single_frame_lasts_a_second():
    assert glasses.frame_durations(["000000000.jpg"]) == [1.0]


def test_frames_are_named_by_time_since_the_first():
    assert glasses.frame_name(100.0, 102.25) == "000002250.jpg"


def test_a_frame_taken_before_the_first_is_named_zero():
    assert glasses.frame_name(100.0, 99.0) == "000000000.jpg"
