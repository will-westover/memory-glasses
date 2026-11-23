"""Link things seen in a moment to the same things seen at the same place before, by comparing their photos."""

import base64
from concurrent.futures import ThreadPoolExecutor

import cv2
from pydantic import BaseModel, Field

from memory import db, llm, storage
from memory.config import CAPTION_MODEL, DATA_DIR

MIN_SIMILARITY = 0.6
CANDIDATES = 3
CROP_PIXELS = 384
CROP_MARGIN = 0.15
PARALLEL_CALLS = 8
LINK_MODEL = CAPTION_MODEL

INSTRUCTIONS = """\
You compare photos of things seen by someone's camera glasses at the same place on different days. The first image
is a thing just seen; each later image is a candidate seen before. Decide whether the first is the very same object
as one of the candidates (the same mug, the same plant), not just the same kind of thing. Look at colour, shape,
pattern, wear, writing and what it sits on. Two things of the same kind that differ in any of these are different.
Say none when anything conflicts, when a crop is too small or blurred to compare, or when the match rests only on
the setting rather than on the object itself. A wrong match is worse than a missed one."""


class Match(BaseModel):
    same_as: int | None = Field(description="The number of the candidate that is the very same object, or null")
    reason: str = Field(description="One short sentence on what decided it")


def crop(image_path: str, box: list[float]) -> str | None:
    """Return a small JPEG of the boxed part of a photo, with some margin, as a data link; None if it can't be read."""
    path = storage.local_copy(DATA_DIR / image_path)
    image = cv2.imread(str(path)) if path else None

    if image is None:
        return None

    height, width = image.shape[:2]
    left, top, box_width, box_height = box
    x0, y0 = max(0.0, left - CROP_MARGIN * box_width), max(0.0, top - CROP_MARGIN * box_height)
    x1, y1 = min(1.0, left + box_width * (1 + CROP_MARGIN)), min(1.0, top + box_height * (1 + CROP_MARGIN))
    piece = image[int(y0 * height) : int(y1 * height), int(x0 * width) : int(x1 * width)]

    if piece.size == 0:
        return None

    scale = CROP_PIXELS / max(piece.shape[:2])
    piece = cv2.resize(piece, None, fx=scale, fy=scale) if scale < 1 else piece
    ok, jpeg = cv2.imencode(".jpg", piece, [cv2.IMWRITE_JPEG_QUALITY, 85])

    return f"data:image/jpeg;base64,{base64.b64encode(jpeg.tobytes()).decode()}" if ok else None


def label(thing: dict) -> str:
    return f"{thing['name']} ({thing['details'] or 'no details'})"


def compare(thing: dict, candidates: list[dict]):
    """Ask a vision model which candidate, if any, is the same object as the thing. Return (Match, usage)."""
    first = crop(thing["image_path"], thing["box"])
    shown = [
        (number, candidate, crop(candidate["image_path"], candidate["box"]))
        for number, candidate in enumerate(candidates, start=1)
    ]
    shown = [(number, candidate, image) for number, candidate, image in shown if image]

    if not first or not shown:
        return None, None

    content = [
        {"type": "input_text", "text": f"Just seen: {label(thing)}"},
        {"type": "input_image", "image_url": first, "detail": "low"},
    ]

    for number, candidate, image in shown:
        content += [
            {"type": "input_text", "text": f"Candidate {number}: {label(candidate)}"},
            {"type": "input_image", "image_url": image, "detail": "low"},
        ]

    response = llm.client().responses.parse(
        model=LINK_MODEL,
        instructions=INSTRUCTIONS,
        input=[{"role": "user", "content": content}],
        text_format=Match,
        reasoning={"effort": "medium"},
    )

    return response.output_parsed, response.usage


def link_moment(conn, moment_id: str) -> dict[str, int]:
    """Link each thing first seen in a moment to the same thing seen at the same place in other moments."""
    place = db.moment_place(conn, moment_id)
    things = db.moment_things(conn, moment_id)

    if place is None or not things:
        return {"things": len(things), "compared": 0, "linked": 0}

    llm.require_price(LINK_MODEL)
    pairs = [
        (thing, candidates)
        for thing in things
        if (candidates := db.link_candidates(conn, thing["id"], moment_id, place["id"], MIN_SIMILARITY, CANDIDATES))
    ]

    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        results = list(pool.map(lambda pair: compare(*pair), pairs))

    linked = 0

    for (thing, candidates), (match, usage) in zip(pairs, results):
        if usage is not None:
            llm.log_cost(conn, "link", LINK_MODEL, usage)

        if match and match.same_as and 0 < match.same_as <= len(candidates):
            db.link_things(conn, thing["id"], candidates[match.same_as - 1]["id"], match.reason)
            linked += 1

    conn.commit()

    return {"things": len(things), "compared": len(pairs), "linked": linked}
