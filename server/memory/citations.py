"""Citation markers in answers, and the answer text inside JSON that is still arriving."""

import json
import re

MARKER = re.compile(r"\[(\d+)\]")
OPEN_MARKER = re.compile(r"\[\d*$")


def renumber(text: str, cited: list[int], known) -> tuple[str, list[int]]:
    """Turn [fact number] markers into [1], [2] in order of first use, dropping unknown facts. Return text and ids."""
    kept: list[int] = []

    for match in MARKER.finditer(text):
        fact_id = int(match.group(1))

        if fact_id in known and fact_id not in kept:
            kept.append(fact_id)

    kept += [fact_id for fact_id in dict.fromkeys(cited) if fact_id in known and fact_id not in kept]

    def replace(match: re.Match) -> str:
        fact_id = int(match.group(1))

        return f"[{kept.index(fact_id) + 1}]" if fact_id in kept else ""

    return re.sub(r" +([.,;:!?])", r"\1", MARKER.sub(replace, text)).replace("  ", " "), kept


def shown_so_far(text: str, known) -> str:
    """Return the renumbered answer text that is safe to show, holding back a marker still being written."""
    return renumber(OPEN_MARKER.sub("", text), [], known)[0]


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
