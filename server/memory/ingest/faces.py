"""Find faces in a moment's frames and group them into people.

uv run python -m memory.ingest.faces <moment id>
"""

import argparse
import sys

import cv2
import numpy as np

from memory import db, storage
from memory.config import DATA_DIR, SERVER_DIR

MODELS_DIR = SERVER_DIR / "models"
DETECTOR_MODEL = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = MODELS_DIR / "face_recognition_sface_2021dec.onnx"

MIN_DETECTION_SCORE = 0.85
MIN_FACE_PIXELS = 40
SAME_PERSON_SIMILARITY = 0.363
MIN_EYE_SPAN = 0.3
MAX_NOSE_OFFSET = 0.5
MIN_SHARPNESS = 22
EDGE_MARGIN = 0.02
SHARPNESS_SIZE = 112


def load_models():
    """Return the face detector and the face recognizer."""
    for path in (DETECTOR_MODEL, RECOGNIZER_MODEL):
        if not path.exists():
            raise SystemExit(f"error: missing {path}. Download it from github.com/opencv/opencv_zoo")

    detector = cv2.FaceDetectorYN.create(str(DETECTOR_MODEL), "", (320, 320), MIN_DETECTION_SCORE)
    recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER_MODEL), "")

    return detector, recognizer


def facing_camera(row) -> bool:
    """Return whether a detected face looks toward the camera, judged by its eye spacing and nose position."""
    eye_span = float(np.hypot(row[4] - row[6], row[5] - row[7]))
    nose_offset = abs(row[8] - (row[4] + row[6]) / 2) / max(eye_span, 1.0)

    return eye_span / row[2] >= MIN_EYE_SPAN and nose_offset <= MAX_NOSE_OFFSET


def sharpness(image, box: tuple) -> float:
    """Return how much fine detail a face has: the variance of its edges at a fixed size. Blurry faces score low."""
    height, width = image.shape[:2]
    x, y, w, h = box
    left, top = max(0, int(x * width)), max(0, int(y * height))
    right, bottom = min(width, int((x + w) * width)), min(height, int((y + h) * height))

    if right - left < 2 or bottom - top < 2:
        return 0.0

    gray = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY)

    return float(cv2.Laplacian(cv2.resize(gray, (SHARPNESS_SIZE, SHARPNESS_SIZE)), cv2.CV_64F).var())


def cut_off(landmarks: list[tuple[float, float]], margin: float = EDGE_MARGIN) -> bool:
    """Return whether the eyes, nose or mouth sit at the edge of the photo, as fractions of it, so the face is cut off."""
    return any(not (margin <= x <= 1 - margin and margin <= y <= 1 - margin) for x, y in landmarks)


def landmarks(row, width: int, height: int) -> list[tuple[float, float]]:
    """Return a detection's eyes, nose and mouth corners as fractions of the photo."""
    return [(float(row[4 + 2 * point]) / width, float(row[5 + 2 * point]) / height) for point in range(5)]


def good_enough(image, row) -> bool:
    """Return whether a detected face is whole and sharp enough to recognise someone by."""
    height, width = image.shape[:2]
    box = (row[0] / width, row[1] / height, row[2] / width, row[3] / height)

    return not cut_off(landmarks(row, width, height)) and sharpness(image, box) >= MIN_SHARPNESS


def faces_in(image, detector, recognizer) -> list[tuple]:
    """Return each clear face as (box as fractions of the image, score, unit-length embedding, facing the camera)."""
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, rows = detector.detect(image)

    faces = []

    for row in rows if rows is not None else []:
        x, y, w, h, score = row[0], row[1], row[2], row[3], row[14]

        box = (x / width, y / height, w / width, h / height)

        if min(w, h) < MIN_FACE_PIXELS or not good_enough(image, row):
            continue

        aligned = recognizer.alignCrop(image, row)
        embedding = recognizer.feature(aligned).flatten()
        embedding = embedding / np.linalg.norm(embedding)

        faces.append((box, float(score), embedding, facing_camera(row)))

    return faces


def best_match(
    embedding: np.ndarray, person_sums: dict[int, np.ndarray], threshold: float = SAME_PERSON_SIMILARITY
) -> int | None:
    """Return the person whose average face is most similar, if similar enough to be the same person."""
    best, best_score = None, threshold

    for person_id, embedding_sum in person_sums.items():
        score = float(np.dot(embedding, embedding_sum / np.linalg.norm(embedding_sum)))

        if score >= best_score:
            best, best_score = person_id, score

    return best


def readable_images(frames: list[tuple]):
    """Yield (frame id, image) for each frame whose image can be read."""
    for frame_id, image_path in frames:
        image = cv2.imread(str(DATA_DIR / image_path))

        if image is not None:
            yield frame_id, image


def pgvector_text(embedding: np.ndarray) -> str:
    """Return an embedding as text Postgres can store."""
    return "[" + ",".join(f"{value:.6f}" for value in embedding) + "]"


def find_faces(conn, moment_id: str) -> dict[str, int]:
    """Replace the moment's faces and assign each one to a person, creating people as needed."""
    detector, recognizer = load_models()
    storage.fetch_folder(DATA_DIR / "moments" / moment_id)
    frames = db.frame_images(conn, moment_id)

    db.clear_faces(conn, moment_id)
    person_sums = db.person_embedding_sums(conn)
    counts = {"frames": len(frames), "faces": 0, "new_people": 0, "skipped": 0}

    detections = [
        (frame_id, face)
        for frame_id, image in readable_images(frames)
        for face in faces_in(image, detector, recognizer)
    ]
    detections.sort(key=lambda item: not item[1][3])

    for frame_id, (box, score, embedding, facing) in detections:
        person_id = best_match(embedding, person_sums)

        if person_id is None and not facing:
            counts["skipped"] += 1
            continue

        if person_id is None:
            person_id = db.new_person(conn)
            person_sums[person_id] = embedding.copy()
            counts["new_people"] += 1
        else:
            person_sums[person_id] = person_sums[person_id] + embedding

        db.save_face(conn, frame_id, person_id, box, score, pgvector_text(embedding))
        counts["faces"] += 1

    db.remove_faceless_people(conn)
    conn.commit()

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and group the faces in a moment.")
    parser.add_argument("moment_id")
    args = parser.parse_args()

    with db.connect() as conn:
        counts = find_faces(conn, args.moment_id)

    print(
        f"Found {counts['faces']} faces in {counts['frames']} frames; {counts['new_people']} new people;"
        f" {counts['skipped']} side views matched no one and were left out."
    )
    print("Name them with: uv run python -m memory.people list")

    return 0


if __name__ == "__main__":
    sys.exit(main())
