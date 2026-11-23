"""Link things seen in a moment to the same things seen at the same place before, by comparing their photos. Only things
that could be recognised again are compared."""

import base64
from concurrent.futures import ThreadPoolExecutor

import cv2
from pydantic import BaseModel, Field

from server import db, llm, storage
from server.config import CAPTION_MODEL, DATA_DIR, SUMMARY_MODEL

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
Say none when anything conflicts, when a crop is too small or blurred to compare, when the match rests only on
the setting rather than on the object itself, or when more than one candidate could be it. Say how sure you are.
A wrong match is worse than a missed one."""


DISTINCT_INSTRUCTIONS = """\
Below are things someone's camera glasses saw, each with a number, a name and details. Say which are a particular
object that could be found again in a later photo: furniture, appliances, fixtures, plants, pictures, devices,
clothes, containers and the like, even plain ones (a desk, a TV stand, blinds, a sofa). Leave out what is not one
object or is too vague to find again: bare surfaces and materials (a wall, a floor, a wooden panel, a patch of
fabric), parts of something, light, shadows, and shapes described only by colour or outline."""


class Distinct(BaseModel):
    numbers: list[int] = Field(description="Numbers of the things that could be recognised again as the same one")


def distinct_things(conn, things: list[dict]) -> list[dict]:
    """Ask a text model which things could be recognised again as the very same object, and return those."""
    if not things:
        return []

    listed = "\n".join(f"{number}. {label(thing)}" for number, thing in enumerate(things, start=1))
    response = llm.client().responses.parse(
        model=SUMMARY_MODEL,
        instructions=DISTINCT_INSTRUCTIONS,
        input=listed,
        text_format=Distinct,
        reasoning={"effort": "low"},
    )
    llm.log_cost(conn, "link-distinct", SUMMARY_MODEL, response.usage)
    chosen = set(response.output_parsed.numbers) if response.output_parsed else set()

    return [thing for number, thing in enumerate(things, start=1) if number in chosen]


class Match(BaseModel):
    same_as: int | None = Field(description="The number of the candidate that is the very same object, or null")
    certain: bool = Field(description="True only if the photos leave no real doubt")
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

    if not things:
        return {"things": 0, "compared": 0, "linked": 0}

    llm.require_price(LINK_MODEL)
    llm.require_price(SUMMARY_MODEL)
    pairs = [
        (thing, candidates)
        for thing in distinct_things(conn, things)
        if (candidates := db.link_candidates(conn, thing["id"], moment_id, place["id"] if place else None, CANDIDATES))
    ]

    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        results = list(pool.map(lambda pair: compare(*pair), pairs))

    chosen: dict[int, list[tuple]] = {}

    for (thing, candidates), (match, usage) in zip(pairs, results):
        if usage is not None:
            llm.log_cost(conn, "link", LINK_MODEL, usage)

        if match and match.certain and match.same_as and 0 < match.same_as <= len(candidates):
            chosen.setdefault(candidates[match.same_as - 1]["id"], []).append((thing["id"], match.reason))

    linked = 0

    for earlier, claims in chosen.items():
        if len(claims) == 1:
            db.link_things(conn, claims[0][0], earlier, claims[0][1])
            linked += 1

    conn.commit()

    return {"things": len(things), "compared": len(pairs), "linked": linked}
