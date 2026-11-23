"""Who spoke: each transcript line has a voiceprint, you name the speaker of one line, and every line with a voice
alike enough gets the same name, in every recording.

uv run python -m server.voices fill        give lines without a voiceprint one, from their recording's audio
uv run python -m server.voices list        show each named speaker and how many lines are theirs
"""

import argparse
import sys

import numpy as np

from server import db
from server.ingest import speech
from server.ingest.facts import refresh_speech_facts
from server.ingest.transcript import SAME_VOICE
from server.shared import storage
from server.shared.files import MOMENTS_DIR


def vector(text: str) -> np.ndarray:
    return np.array([float(value) for value in text.strip("[]").split(",")])


def fill_voices(conn) -> int:
    """Give every transcript line without a voiceprint one, from its recording's audio. Return how many got one."""
    rows = conn.execute(
        "select a.id, a.moment_id, a.offset_seconds, a.offset_seconds + extract(epoch from a.end_at - a.start_at)"
        "  from audio_segment a where a.voice is null order by a.moment_id"
    ).fetchall()
    filled, audio = 0, {}

    for line_id, moment_id, start, end in rows:
        if moment_id not in audio:
            path = storage.local_copy(MOMENTS_DIR / moment_id / "audio.wav")
            audio[moment_id] = speech.load_audio(path) if path else None

        samples = audio[moment_id]
        voice = speech.voice_embedding(speech.clip(samples, float(start), float(end))) if samples is not None else None

        if voice is not None:
            conn.execute("update audio_segment set voice = %s where id = %s", (str(voice), line_id))
            filled += 1

    conn.commit()

    return filled


def best_speaker(voice: np.ndarray, people: dict[int, np.ndarray], threshold: float = SAME_VOICE) -> int | None:
    """Return the one person whose voice is alike enough to this one; None if nobody is, or more than one is."""
    alike = [person for person, centre in people.items() if float(voice @ centre) >= threshold]

    return alike[0] if len(alike) == 1 else None


def spread_names(conn, moment_id: str | None = None) -> list[str]:
    """Name every line you didn't name yourself, in one moment or all of them, by the one person whose named lines
    sound alike, and return the moments whose names changed."""
    named = conn.execute(
        "select person_id, voice::text from audio_segment where named_by_you and person_id is not null and voice is not null"
    ).fetchall()
    sums: dict[int, np.ndarray] = {}

    for person_id, voice in named:
        sums[person_id] = sums.get(person_id, 0) + vector(voice)

    people = {person_id: total / np.linalg.norm(total) for person_id, total in sums.items()}
    changed = set()

    for line_id, line_moment, voice, current in conn.execute(
        "select id, moment_id, voice::text, person_id from audio_segment"
        " where not named_by_you and voice is not null and (%(moment)s::text is null or moment_id = %(moment)s)",
        {"moment": moment_id},
    ).fetchall():
        speaker = best_speaker(vector(voice), people)

        if speaker != current:
            conn.execute("update audio_segment set person_id = %s where id = %s", (speaker, line_id))
            changed.add(line_moment)

    return sorted(changed)


def name_lines(conn, line_ids: list[int], person_id: int | None) -> list[str]:
    """Say who spoke some lines (or that you don't know), name every line with a voice like theirs, rewrite the speech
    facts of the moments that changed, and return those moments."""
    named = conn.execute(
        "update audio_segment set person_id = %s, named_by_you = %s where id = any(%s) returning moment_id",
        (person_id, person_id is not None, line_ids),
    ).fetchall()

    if not named:
        return []

    moments = sorted(set(spread_names(conn)) | {moment_id for (moment_id,) in named})

    for moment_id in moments:
        refresh_speech_facts(conn, moment_id)

    conn.commit()

    return moments


def main() -> int:
    parser = argparse.ArgumentParser(description="Voiceprints and speaker names.")
    parser.add_argument("command", choices=["fill", "list"])
    args = parser.parse_args()

    with db.connect() as conn:
        if args.command == "fill":
            print(f"{fill_voices(conn)} lines got a voiceprint")

        for name, lines, yours in conn.execute(
            "select p.name, count(*), count(*) filter (where a.named_by_you) from audio_segment a"
            " join person p on p.id = a.person_id group by p.name order by 2 desc"
        ).fetchall():
            print(f"  {name or '(unnamed)':<24} {lines:>4} lines, {yours} named by you")

    return 0


if __name__ == "__main__":
    sys.exit(main())
