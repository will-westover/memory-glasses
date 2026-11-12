"""Blur scores, used to pick the sharpest frame for each moment and drop moments that are blurred throughout.

A frame's blur score is the share of its fine detail that survives being blurred again. A sharp frame loses
most of its detail when blurred, so it scores low (around 0.3). A frame that was already blurry has little left
to lose, so it scores high (0.45 and up). Because it is a ratio, a sharp photo of a plain wall still scores low.

For each slot of time the recording is sampled several times, and the sharpest sample stands for that slot. A
slot is dropped only when even its sharpest sample is over the limit.
"""

# How many frames are sampled in each slot, to choose the sharpest from.
CANDIDATES_PER_SLOT = 4

# A slot whose sharpest frame scores above this is dropped. Set from two test videos: across 66 slots the sharp
# ones scored 0.29 to 0.40 and the one visibly smeared slot scored 0.45. It sits at the blurry end of that gap
# on purpose, so that a doubtful frame is kept. Raise it to keep more.
DEFAULT_MAX_BLUR = 0.44

# Below this much detail the frame is blank (a black or washed-out frame) and has no score.
MIN_DETAIL = 0.05


def blur_scores(detail: list[float], detail_after_blur: list[float]) -> list[float | None]:
    """Return each frame's blur score, or None for a blank frame.

    detail is the average edge strength of each frame; detail_after_blur is the same after blurring the frame.
    """
    return [
        after / before if before >= MIN_DETAIL else None
        for before, after in zip(detail, detail_after_blur)
    ]


def sharpest_per_slot(
    scores: list[float | None],
    per_slot: int = CANDIDATES_PER_SLOT,
    max_blur: float = DEFAULT_MAX_BLUR,
) -> list[int | None]:
    """Return, for each slot, the position of its sharpest frame, or None if the whole slot is too blurry.

    scores holds every sampled frame in order, per_slot of them for each slot. The last slot may be short.
    """
    chosen: list[int | None] = []

    for start in range(0, len(scores), per_slot):
        usable = [
            (score, position)
            for position, score in enumerate(scores[start : start + per_slot], start=start)
            if score is not None
        ]

        best = min(usable) if usable else None

        chosen.append(best[1] if best and best[0] <= max_blur else None)

    return chosen
