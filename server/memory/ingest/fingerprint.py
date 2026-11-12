"""Frame fingerprints, used to drop frames that look the same as the last one kept.

A fingerprint is 64 bits. The frame is shrunk to 9 x 8 grey pixels, and each bit records whether a pixel is
brighter than the one to its right. Two frames of the same scene differ in only a few bits.
"""

THUMB_WIDTH = 9
THUMB_HEIGHT = 8
THUMB_BYTES = THUMB_WIDTH * THUMB_HEIGHT

# Frames whose fingerprints differ in this many bits or fewer count as the same scene.
DEFAULT_MAX_DISTANCE = 6


def fingerprint(grey: bytes) -> int:
    """Return the 64-bit fingerprint of a 9 x 8 grey thumbnail, given as 72 bytes in row order."""
    if len(grey) != THUMB_BYTES:
        raise ValueError(f"expected {THUMB_BYTES} bytes, got {len(grey)}")

    bits = 0
    for row in range(THUMB_HEIGHT):
        start = row * THUMB_WIDTH
        for col in range(THUMB_WIDTH - 1):
            brighter = grey[start + col] > grey[start + col + 1]
            bits = (bits << 1) | brighter

    return bits


def distance(a: int, b: int) -> int:
    """Return how many bits differ between two fingerprints."""
    return (a ^ b).bit_count()


def indexes_to_keep(fingerprints: list[int], max_distance: int = DEFAULT_MAX_DISTANCE) -> list[int]:
    """Return the positions of the frames worth keeping.

    Each frame is compared with the last frame kept, not the one just before it, so a slow change still
    produces a new frame once it adds up.
    """
    kept: list[int] = []

    for index, current in enumerate(fingerprints):
        if not kept or distance(current, fingerprints[kept[-1]]) > max_distance:
            kept.append(index)

    return kept
