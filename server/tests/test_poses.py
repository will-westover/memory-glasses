from memory.ingest.poses import nearest_pose, thin

POSES = [[0.0, 0, 0, 0, 0], [0.1, 1, 0, 0, 0], [0.2, 2, 0, 0, 0], [5.0, 3, 0, 0, 0]]


def test_the_closest_pose_in_time_is_used():
    assert nearest_pose(POSES, 0.14)[1] == 1
    assert nearest_pose(POSES, 0.16)[1] == 2


def test_no_pose_when_none_is_close_enough():
    assert nearest_pose(POSES, 2.5) is None
    assert nearest_pose([], 1.0) is None


def test_a_long_path_is_thinned_but_keeps_its_end():
    poses = [[float(i), i, 0, 0, 0] for i in range(1001)]

    thinned = thin(poses, limit=100)

    assert len(thinned) <= 102
    assert thinned[0] == poses[0] and thinned[-1] == poses[-1]
