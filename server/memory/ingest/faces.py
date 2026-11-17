"""Find faces in a session's frames and group them into people.

    uv run python -m memory.ingest.faces <session id>
"""

import argparse
import sys

import cv2
import numpy as np

from memory import db
from memory.config import DATA_DIR, SERVER_DIR

MODELS_DIR = SERVER_DIR / "models"
DETECTOR = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER = MODELS_DIR / "face_recognition_sface_2021dec.onnx"

MIN_SCORE = 0.85
MIN_FACE_PIXELS = 40
SAME_PERSON = 0.363
MIN_EYE_SPAN = 0.3
MAX_NOSE_OFFSET = 0.5


def load_models():
    """Return the face detector and the face recognizer."""
    for path in (DETECTOR, RECOGNIZER):
        if not path.exists():
            raise SystemExit(f"error: missing {path}. Download it from github.com/opencv/opencv_zoo")

    detector = cv2.FaceDetectorYN.create(str(DETECTOR), "", (320, 320), MIN_SCORE)
    recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER), "")

    return detector, recognizer


def facing_camera(row) -> bool:
    """Return whether a detected face looks toward the camera, judged by its eye spacing and nose position."""
    eye_span = float(np.hypot(row[4] - row[6], row[5] - row[7]))
    nose_offset = abs(row[8] - (row[4] + row[6]) / 2) / max(eye_span, 1.0)

    return eye_span / row[2] >= MIN_EYE_SPAN and nose_offset <= MAX_NOSE_OFFSET


def faces_in(image, detector, recognizer) -> list[tuple]:
    """Return each clear face as (box as fractions of the image, score, unit-length embedding, facing the camera)."""
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, rows = detector.detect(image)

    found = []

    for row in rows if rows is not None else []:
        x, y, w, h, score = row[0], row[1], row[2], row[3], row[14]

        if min(w, h) < MIN_FACE_PIXELS:
            continue

        aligned = recognizer.alignCrop(image, row)
        embedding = recognizer.feature(aligned).flatten()
        embedding = embedding / np.linalg.norm(embedding)

        found.append(((x / width, y / height, w / width, h / height), float(score), embedding, facing_camera(row)))

    return found


def best_match(embedding: np.ndarray, people: dict[int, np.ndarray], threshold: float = SAME_PERSON) -> int | None:
    """Return the person whose average face is most similar, if similar enough to be the same person."""
    best, best_score = None, threshold

    for person_id, centre in people.items():
        score = float(np.dot(embedding, centre / np.linalg.norm(centre)))

        if score >= best_score:
            best, best_score = person_id, score

    return best


def images(frames: list[tuple]):
    """Yield (frame id, image) for each frame whose image can be read."""
    for frame_id, image_path in frames:
        image = cv2.imread(str(DATA_DIR / image_path))

        if image is not None:
            yield frame_id, image


def vector(embedding: np.ndarray) -> str:
    """Return an embedding as text Postgres can store."""
    return "[" + ",".join(f"{value:.6f}" for value in embedding) + "]"


def find_faces(conn, session_id: str) -> dict[str, int]:
    """Replace the session's faces and assign each one to a person, creating people as needed."""
    detector, recognizer = load_models()
    frames = db.frame_images(conn, session_id)

    db.clear_faces(conn, session_id)
    people = db.person_centres(conn)
    counts = {"frames": len(frames), "faces": 0, "new_people": 0, "skipped": 0}

    found = [(frame_id, face) for frame_id, image in images(frames) for face in faces_in(image, detector, recognizer)]
    found.sort(key=lambda item: not item[1][3])

    for frame_id, (box, score, embedding, frontal) in found:
        person_id = best_match(embedding, people)

        if person_id is None and not frontal:
            counts["skipped"] += 1
            continue

        if person_id is None:
            person_id = db.new_person(conn)
            people[person_id] = embedding.copy()
            counts["new_people"] += 1
        else:
            people[person_id] = people[person_id] + embedding

        db.save_face(conn, frame_id, person_id, box, score, vector(embedding))
        counts["faces"] += 1

    db.remove_faceless_people(conn)
    conn.commit()

    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Find and group the faces in a session.")
    parser.add_argument("session_id")
    args = parser.parse_args()

    with db.connect() as conn:
        counts = find_faces(conn, args.session_id)

    print(f"Found {counts['faces']} faces in {counts['frames']} frames; {counts['new_people']} new people;"
          f" {counts['skipped']} side views matched no one and were left out.")
    print("Name them with: uv run python -m memory.people list")

    return 0


if __name__ == "__main__":
    sys.exit(main())
