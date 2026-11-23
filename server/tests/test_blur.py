from server.ingest.blur import blur_scores, sharpest_per_slot


def test_score_is_the_share_of_detail_left_after_blurring():
    assert blur_scores([10.0, 4.0], [3.0, 2.0]) == [0.3, 0.5]


def test_a_blank_frame_has_no_score():
    assert blur_scores([0.0], [0.0]) == [None]


def test_each_slot_gives_its_sharpest_frame():
    scores = [0.40, 0.31, 0.35, 0.38, 0.33, 0.36, 0.30, 0.41]

    assert sharpest_per_slot(scores, per_slot=4, max_blur=0.44) == [1, 6]


def test_a_slot_is_dropped_only_when_every_frame_in_it_is_blurry():
    one_sharp = [0.50, 0.52, 0.39, 0.48]
    all_blurry = [0.50, 0.52, 0.47, 0.48]

    assert sharpest_per_slot(one_sharp + all_blurry, per_slot=4, max_blur=0.44, margin=0) == [2, None]


def test_blank_frames_are_never_chosen_and_a_short_last_slot_still_counts():
    scores = [None, 0.35, 0.33, 0.34, None]

    assert sharpest_per_slot(scores, per_slot=4, max_blur=0.44) == [2, None]


def test_a_soft_recording_keeps_its_frames_and_drops_only_the_clear_outlier():
    scores = [0.46, 0.47, 0.48, 0.49, 0.45, 0.46, 0.47, 0.48, 0.47, 0.50, 0.51, 0.52, 0.58, 0.60, 0.61, 0.62]

    chosen = sharpest_per_slot(scores, per_slot=4)

    assert chosen == [0, 4, 8, None]
