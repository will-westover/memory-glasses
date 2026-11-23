"""A moment's kept files: its manifest, video and extras, fetched from storage when needed."""

import json

from server import storage
from server.config import DATA_DIR

MOMENTS_DIR = (DATA_DIR / "moments").resolve()


def manifest_of(moment_id: str) -> dict:
    """Return a moment's manifest, or an empty one if it can't be read."""
    path = (MOMENTS_DIR / moment_id / "manifest.json").resolve()

    if not path.is_relative_to(MOMENTS_DIR) or storage.local_copy(path) is None:
        return {}

    return json.loads(path.read_text())


def video_path(moment_id: str, download: bool = True):
    """Return the path of a moment's original video, or None if it wasn't kept."""
    folder = (MOMENTS_DIR / moment_id).resolve()
    video_file = manifest_of(moment_id).get("video_file")

    if not folder.is_relative_to(MOMENTS_DIR) or not video_file:
        return None

    path = folder / video_file

    if not download:
        return path if storage.exists(path) else None

    return storage.local_copy(path)


def has_video(moment_id: str) -> bool:
    return video_path(moment_id, download=False) is not None


def current_extras(moment_id: str) -> dict:
    """Return the extra files kept with a moment, by name."""
    folder = MOMENTS_DIR / moment_id

    return {
        name: folder / name
        for name in manifest_of(moment_id).get("extras", [])
        if storage.local_copy(folder / name) is not None
    }
