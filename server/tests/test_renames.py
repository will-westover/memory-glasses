from server.names import replaced


def test_a_name_is_swapped_only_as_a_whole_word():
    assert replaced("With Friend 1 and Friend 12. Friend 1 waved.", "Friend 1", "Sam") == "With Sam and Friend 12. Sam waved."


def test_a_name_inside_another_word_is_left_alone():
    assert replaced("Meet Me at Mel's", "Me", "Will") == "Meet Will at Mel's"


def test_a_room_label_is_swapped_but_the_word_elsewhere_is_not():
    text = "A bed in the Bedroom corner. Room: Bedroom. At Home."

    assert replaced(text, "Room: Bedroom", "Room: Guest Room") == "A bed in the Bedroom corner. Room: Guest Room. At Home."


def test_nothing_to_swap_in_an_empty_title():
    assert replaced(None, "Me", "Will") is None
