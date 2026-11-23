"""Where things are in a room, for phone recordings with a lidar mesh: the middle of each thing's box is traced from
the camera onto the mesh, giving its spot in the recording's own map, in metres."""

import argparse
import sys

import numpy as np

from server import db
from server.shared import storage
from server.shared.files import MOMENTS_DIR


def load_mesh(path) -> np.ndarray:
    """Return the triangles of an OBJ mesh as an array of shape (triangles, 3 corners, xyz)."""
    vertices, faces = [], []

    with open(path) as mesh:
        for line in mesh:
            if line.startswith("v "):
                vertices.append([float(value) for value in line.split()[1:4]])
            elif line.startswith("f "):
                faces.append([int(corner.split("/")[0]) - 1 for corner in line.split()[1:4]])

    return np.array(vertices)[np.array(faces)] if faces else np.zeros((0, 3, 3))


def sensor_point(u: float, v: float) -> tuple[float, float]:
    """Turn a point in a kept photo (portrait, 0 to 1) into the same point in the camera's landscape image."""
    return v, 1 - u


def camera_ray(camera: list[float], u: float, v: float) -> tuple[np.ndarray, np.ndarray]:
    """Return where the camera was and the direction it saw a point of a kept photo, in the recording's map."""
    transform = np.array(camera[:16]).reshape(4, 4).T
    fx, fy, cx, cy, width, height = camera[16:22]
    sx, sy = sensor_point(u, v)
    seen = np.array([(sx * width - cx) / fx, -(sy * height - cy) / fy, -1.0])
    direction = transform[:3, :3] @ seen

    return transform[:3, 3], direction / np.linalg.norm(direction)


def first_hit(origin: np.ndarray, direction: np.ndarray, triangles: np.ndarray) -> float | None:
    """Return how far along a ray it first meets the mesh, or None if it misses (Moller-Trumbore)."""
    if not len(triangles):
        return None

    corner, edge1, edge2 = triangles[:, 0], triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    across = np.cross(direction, edge2)
    determinant = np.einsum("ij,ij->i", edge1, across)
    usable = np.abs(determinant) > 1e-9
    inverse = np.where(usable, 1 / np.where(usable, determinant, 1), 0)
    offset = origin - corner
    u = np.einsum("ij,ij->i", offset, across) * inverse
    turned = np.cross(offset, edge1)
    v = (turned @ direction) * inverse
    distance = np.einsum("ij,ij->i", edge2, turned) * inverse
    hits = usable & (u >= 0) & (v >= 0) & (u + v <= 1) & (distance > 0)

    return float(distance[hits].min()) if hits.any() else None


def spot(camera: list[float], box: list[float], triangles: np.ndarray) -> list[float] | None:
    """Return where the middle of a box lands on the mesh, or None if the ray misses it."""
    left, top, width, height = box
    origin, direction = camera_ray(camera, left + width / 2, top + height / 2)
    distance = first_hit(origin, direction, triangles)

    return (origin + distance * direction).round(3).tolist() if distance is not None else None


def place_things(conn, moment_id: str) -> int:
    """Give each boxed sighting in a phone recording with a lidar mesh its spot, and each thing the middle of its
    spots. Return how many sightings were placed."""
    sightings = db.boxed_sightings_with_camera(conn, moment_id)
    mesh = storage.local_copy(MOMENTS_DIR / moment_id / "mesh.obj") if sightings else None

    if mesh is None:
        return 0

    triangles = load_mesh(mesh)
    spots = {sighting_id: spot(camera, box, triangles) for sighting_id, camera, box in sightings}
    placed = {sighting_id: where for sighting_id, where in spots.items() if where}
    db.save_spots(conn, placed)

    return len(placed)


def main() -> int:
    parser = argparse.ArgumentParser(description="Place a phone recording's things on its lidar mesh and show how tightly "
                                     "each thing's spots agree, which shows whether the photos are read the right way round.")
    parser.add_argument("moment_id")
    args = parser.parse_args()

    with db.connect() as conn:
        print(f"{place_things(conn, args.moment_id)} sightings placed")
        conn.commit()

        for name, spread, count in db.spot_spread(conn, args.moment_id):
            print(f"  {name:<40} {count:>3} sightings, within {spread:.2f} m of their middle")

    return 0


if __name__ == "__main__":
    sys.exit(main())
