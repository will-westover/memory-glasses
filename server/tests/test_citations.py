from memory.citations import partial_string, renumber, shown_so_far


def test_fact_numbers_become_positions_in_order_of_first_use():
    text, kept = renumber("Sam said Friday [412], in room 12 [87], twice [412].", [87, 412], {87, 412})

    assert kept == [412, 87]
    assert text == "Sam said Friday [1], in room 12 [2], twice [1]."


def test_a_marker_for_a_fact_never_shown_disappears():
    text, kept = renumber("The sign said exit [99] [5].", [99, 5], {5})

    assert kept == [5]
    assert text == "The sign said exit [1]."


def test_cited_facts_without_markers_still_count_as_sources():
    text, kept = renumber("The sign said exit.", [5, 3, 5], {3, 5})

    assert kept == [5, 3]
    assert text == "The sign said exit."


def test_a_marker_still_being_written_is_held_back():
    assert shown_so_far("The sign said exit [41", {41}) == "The sign said exit "
    assert shown_so_far("The sign said exit [41]", {41}) == "The sign said exit [1]"


def test_partial_string_reads_what_has_arrived():
    assert partial_string('{"answer": "The sign sa', "answer") == "The sign sa"
    assert partial_string('{"answer": "Done.", "found": true', "answer") == "Done."
    assert partial_string('{"answ', "answer") == ""


def test_partial_string_waits_for_a_whole_escape():
    assert partial_string('{"answer": "He said \\"hi', "answer") == 'He said "hi'
    assert partial_string('{"answer": "caf\\u00', "answer") == "caf"
    assert partial_string('{"answer": "line\\', "answer") == "line"
