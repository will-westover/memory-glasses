import pytest

from memory.places import distance_m, parse_location, place_for

HOME = ("Home", 40.2518, -111.6493, 75)
LIBRARY = ("Library", 40.2488, -111.6494, 120)


def test_parses_the_tag_phones_write_into_videos():
    assert parse_location("+40.2518-111.6493+1387.000/") == (40.2518, -111.6493)
    assert parse_location("-33.8688+151.2093/") == (-33.8688, 151.2093)


def test_distance_matches_a_known_value():
    # One degree of latitude is about 111.2 km everywhere.
    assert distance_m(40.0, -111.0, 41.0, -111.0) == pytest.approx(111_195, rel=0.001)


def test_only_a_point_inside_a_radius_gets_that_place():
    assert place_for(40.25185, -111.64935, [HOME, LIBRARY]) == "Home"
    assert place_for(40.30, -111.70, [HOME, LIBRARY]) is None
    assert place_for(None, None, [HOME]) is None


def test_overlapping_places_pick_the_nearest():
    near_library = (40.2489, -111.6494)
    big_campus = ("Campus", 40.2500, -111.6490, 1000)

    assert place_for(*near_library, [big_campus, LIBRARY]) == "Library"
