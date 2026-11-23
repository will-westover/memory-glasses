"""Small copies of kept frames, for lists and strips in the app."""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2

from server import db
from server.shared import storage
from server.shared.config import DATA_DIR, PARALLEL_CALLS

THUMB_PIXELS = 400

THUMB_QUALITY = 75


def thumb_path(frame_path: Path) -> Path:
    """Return where the small copy of a frame is kept: thumbs/ beside its frames/ folder."""
    return frame_path.parent.parent / "thumbs" / frame_path.name


def make_thumb(frame_path: Path) -> Path | None:
    """Write the small copy of one frame and return its path, or None if the frame can't be read."""
    image = cv2.imread(str(frame_path))

    if image is None:
        return None

    scale = THUMB_PIXELS / max(image.shape[:2])
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
    target = thumb_path(frame_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(target), small, [cv2.IMWRITE_JPEG_QUALITY, THUMB_QUALITY])

    return target


def make_thumbs(frames_dir: Path) -> int:
    """Write the small copy of every frame in a folder and return how many were made."""
    return sum(make_thumb(path) is not None for path in sorted(frames_dir.glob("*.jpg")))


def backfill(image_path: str) -> bool:
    """Make and upload the small copy of one stored frame. Return whether it worked."""
    frame = storage.local_copy(DATA_DIR / image_path)
    thumb = make_thumb(frame) if frame else None

    if thumb:
        storage.save(thumb)

    return thumb is not None


def main() -> int:
    argparse.ArgumentParser(description="Make the small copy of every stored frame.").parse_args()

    with db.connect() as conn:
        paths = [path for (path,) in conn.execute("select image_path from frame order by id")]

    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        made = sum(pool.map(backfill, paths))

    print(f"{made} of {len(paths)} frames have a small copy")

    return 0 if made == len(paths) else 1


if __name__ == "__main__":
    sys.exit(main())
