import numpy as np

from server.ingest.spots import camera_ray, first_hit, load_mesh, spot

LOOKING_AHEAD = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 1000.0, 1000.0, 960.0, 720.0, 1920.0, 1440.0]


def wall(z: float) -> np.ndarray:
    return np.array([[[-5, -5, z], [5, -5, z], [5, 5, z]], [[-5, -5, z], [5, 5, z], [-5, 5, z]]], dtype=float)


def test_the_middle_of_the_photo_lands_straight_ahead_on_the_wall():
    assert spot(LOOKING_AHEAD, [0.4, 0.4, 0.2, 0.2], wall(-2.0)) == [0.0, 0.0, -2.0]


def test_a_ray_that_misses_the_mesh_gives_no_spot():
    assert spot(LOOKING_AHEAD, [0.4, 0.4, 0.2, 0.2], wall(2.0)) is None


def test_the_nearest_surface_is_the_one_hit():
    origin, direction = camera_ray(LOOKING_AHEAD, 0.5, 0.5)

    assert first_hit(origin, direction, np.concatenate([wall(-3.0), wall(-1.5)])) == 1.5


def test_the_top_of_a_portrait_photo_is_the_left_of_the_camera_image():
    origin, direction = camera_ray(LOOKING_AHEAD, 0.5, 0.0)

    assert direction[0] < 0 and abs(direction[1]) < 1e-9


def test_an_obj_mesh_is_read_as_triangles(tmp_path):
    (tmp_path / "mesh.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")

    assert load_mesh(tmp_path / "mesh.obj").shape == (1, 3, 3)
