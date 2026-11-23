"""Where the phone was during a recording, from the poses the app saves: [seconds, x, y, z, yaw] in metres, then, in
newer recordings, the camera's full view (see frame_position in db/moments.py)."""

import bisect
import json
import math
from pathlib import Path

MAX_GAP_SECONDS = 0.5
PATH_POINTS = 500


def load_poses(path: Path) -> list[list[float]]:
    """Return the poses in a poses.json file, in time order."""
    return sorted(json.loads(path.read_text())["poses"])


def nearest_pose(poses: list[list[float]], seconds: float, max_gap: float = MAX_GAP_SECONDS) -> list[float] | None:
    """Return the pose closest in time, or None if none is within max_gap seconds."""
    if not poses:
        return None

    times = [pose[0] for pose in poses]
    index = bisect.bisect_left(times, seconds)
    nearby = [poses[i] for i in (index - 1, index) if 0 <= i < len(poses)]
    best = min(nearby, key=lambda pose: abs(pose[0] - seconds))

    return best if abs(best[0] - seconds) <= max_gap else None


def thin(poses: list[list[float]], limit: int = PATH_POINTS) -> list[list[float]]:
    """Return at most about `limit` evenly spaced poses, always keeping the last, as [seconds, x, y, z, yaw]."""
    kept = poses if len(poses) <= limit else poses[:: math.ceil(len(poses) / limit)]
    kept = kept if kept[-1] is poses[-1] else kept + [poses[-1]]

    return [pose[:5] for pose in kept]
