"""Citation markers in answers, and the answer text inside JSON that is still arriving."""

import json
import re

MARKER = re.compile(r"\[(\d+)\]")
OPEN_MARKER = re.compile(r"\[\d*$")


def renumber(text: str, cited: list[int], shown_ids) -> tuple[str, list[int]]:
    """Turn [fact number] markers into [1], [2] in order of first use, dropping unknown facts. Return text and ids."""
    cited_in_order: list[int] = []

    for match in MARKER.finditer(text):
        fact_id = int(match.group(1))

        if fact_id in shown_ids and fact_id not in cited_in_order:
            cited_in_order.append(fact_id)

    cited_in_order += [
        fact_id for fact_id in dict.fromkeys(cited) if fact_id in shown_ids and fact_id not in cited_in_order
    ]

    def replace(match: re.Match) -> str:
        fact_id = int(match.group(1))

        return f"[{cited_in_order.index(fact_id) + 1}]" if fact_id in cited_in_order else ""

    return re.sub(r" +([.,;:!?])", r"\1", MARKER.sub(replace, text)).replace("  ", " "), cited_in_order


def shown_so_far(text: str, shown_ids) -> str:
    """Return the renumbered answer text that is safe to show, holding back a marker still being written."""
    return renumber(OPEN_MARKER.sub("", text), [], shown_ids)[0]


def partial_string(buffer: str, key: str) -> str:
    """Return as much of a string field's value as has arrived in JSON that may be cut off anywhere."""
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"', buffer)

    if not match:
        return ""

    raw = buffer[match.end() :]
    end = 0

    while end < len(raw):
        if raw[end] == "\\":
            end += 2
            continue

        if raw[end] == '"':
            break

        end += 1

    piece = raw[: min(end, len(raw))]

    for cut in range(len(piece), max(len(piece) - 6, -1), -1):
        try:
            return json.loads(f'"{piece[:cut]}"')
        except json.JSONDecodeError:
            continue

    return ""
