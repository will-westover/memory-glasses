"""Blur scores: the share of a frame's fine detail that survives being blurred again. Low is sharp."""

import statistics

CANDIDATES_PER_SLOT = 4

DEFAULT_MAX_BLUR = 0.44
RELATIVE_MARGIN = 0.06

MIN_DETAIL = 0.05


def blur_scores(detail: list[float], detail_after_blur: list[float]) -> list[float | None]:
    """Return each frame's blur score, or None for a blank frame."""
    return [after / before if before >= MIN_DETAIL else None for before, after in zip(detail, detail_after_blur)]


def sharpest_per_slot(
    scores: list[float | None],
    per_slot: int = CANDIDATES_PER_SLOT,
    max_blur: float = DEFAULT_MAX_BLUR,
    margin: float = RELATIVE_MARGIN,
) -> list[int | None]:
    """Return the position of each slot's sharpest frame, or None if it is clearly blurrier than the rest."""
    best: list[tuple[float, int] | None] = []

    for start in range(0, len(scores), per_slot):
        usable = [
            (score, position)
            for position, score in enumerate(scores[start : start + per_slot], start=start)
            if score is not None
        ]
        best.append(min(usable) if usable else None)

    typical = statistics.median(pick[0] for pick in best if pick) if any(best) else max_blur
    limit = max(max_blur, typical + margin)

    return [pick[1] if pick and pick[0] <= limit else None for pick in best]
