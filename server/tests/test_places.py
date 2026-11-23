from server.places import smooth, travelled


def test_a_lone_label_among_neighbours_takes_theirs():
    own = [(1, 0.0, 0.0, "kitchen"), (2, 0.5, 0.0, "entry"), (3, 1.0, 0.0, "kitchen")]

    assert smooth(own, [])[2] == "kitchen"


def test_frames_far_apart_keep_their_own_rooms():
    own = [(1, 0.0, 0.0, "kitchen"), (2, 5.0, 0.0, "bedroom")]

    assert smooth(own, []) == {1: "kitchen", 2: "bedroom"}


def test_a_tie_keeps_the_frames_own_room():
    own = [(1, 0.0, 0.0, "kitchen"), (2, 0.5, 0.0, "entry")]

    assert smooth(own, []) == {1: "kitchen", 2: "entry"}


def test_other_moments_in_the_same_map_vote_too():
    own = [(1, 0.0, 0.0, "hallway")]
    others = [(0.3, 0.0, "living room"), (0.0, 0.4, "living room")]

    assert smooth(own, others) == {1: "living room"}


def test_frames_without_a_position_are_left_alone():
    own = [(1, None, None, "closet"), (2, 0.0, 0.0, "bedroom")]

    assert smooth(own, [])[1] == "closet"


def test_travelled_only_past_a_buildings_size():
    still = [[0, 0.0, 0.0, 0.0, 0.0], [10, 4.0, 0.0, 3.0, 0.0]]
    moving = [[0, 0.0, 0.0, 0.0, 0.0], [10, 40.0, 0.0, 5.0, 0.0]]

    assert not travelled(still)
    assert travelled(moving)
    assert not travelled([])
