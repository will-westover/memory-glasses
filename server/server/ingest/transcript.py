"""Turn a moment's audio into timed lines of text, each with a voiceprint of who spoke."""

import argparse
import base64
import io
import subprocess
import sys
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from server import db
from server.shared import llm, storage
from server.shared.config import DATA_DIR, TRANSCRIBE_MODEL, minutes_seconds, PARALLEL_CALLS
from server.ingest import speech

DIARIZE_MODEL = "gpt-4o-transcribe-diarize"
FALLBACK_MODEL = "gpt-live-transcribe"
LIVE_RATE = 24000
UPLOAD_KBPS = 48
UPLOAD_LIMIT_BYTES = 25 * 1024 * 1024
SAME_VOICE = 0.35


@dataclass
class Line:
    start: float
    end: float
    text: str
    speaker: str | None = None
    voice: list[float] | None = None


@dataclass
class Call:
    usage: object
    seconds: float
    model: str | None = None


def small_mp3(path: Path) -> bytes:
    """Return the audio as a mono MP3 small enough to upload."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-b:a", f"{UPLOAD_KBPS}k", "-f", "mp3", "-"],
        capture_output=True,
        check=True,
    )

    return result.stdout


def wav_bytes(samples: np.ndarray) -> bytes:
    """Return 16 kHz mono samples as a WAV file."""
    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(speech.SAMPLE_RATE)
        file.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())

    return buffer.getvalue()


def diarized(path: Path, samples: np.ndarray, model: str) -> tuple[list[Line], list[Call]]:
    """Transcribe the whole file in one call that returns timed lines with speaker letters."""
    audio = small_mp3(path)

    if len(audio) > UPLOAD_LIMIT_BYTES:
        raise RuntimeError(f"{path.name} is too long to send in one request. Split the moment first.")

    response = llm.client().audio.transcriptions.create(
        model=model,
        file=("audio.mp3", audio, "audio/mpeg"),
        response_format="diarized_json",
        chunking_strategy="auto",
    )

    lines = [
        Line(segment.start, segment.end, segment.text.strip(), segment.speaker)
        for segment in response.segments
        if segment.text.strip()
    ]

    return lines, [Call(response.usage, len(samples) / speech.SAMPLE_RATE, model)]


def pcm24k(samples: np.ndarray) -> bytes:
    """Return 16 kHz samples as 24 kHz 16-bit PCM, the format live transcription takes."""
    count = int(len(samples) * LIVE_RATE / speech.SAMPLE_RATE)
    resampled = np.interp(np.arange(count) * speech.SAMPLE_RATE / LIVE_RATE, np.arange(len(samples)), samples)

    return (np.clip(resampled, -1, 1) * 32767).astype(np.int16).tobytes()


def transcribe_live(samples: np.ndarray, model: str, keywords: list[str]) -> tuple[str, Call]:
    """Transcribe one run of speech through a live transcription session and return its text and call."""
    transcription = {"model": model, "keywords": keywords} if keywords else {"model": model}
    audio_input = {"format": {"type": "audio/pcm", "rate": LIVE_RATE}, "transcription": transcription}
    pcm = pcm24k(samples)

    with llm.client().realtime.connect(extra_query={"intent": "transcription"}) as connection:
        connection.session.update(
            session={"type": "transcription", "audio": {"input": {**audio_input, "turn_detection": None}}}
        )

        for start in range(0, len(pcm), LIVE_RATE * 2):
            connection.input_audio_buffer.append(audio=base64.b64encode(pcm[start : start + LIVE_RATE * 2]).decode())

        connection.input_audio_buffer.commit()

        for event in connection:
            if event.type == "conversation.item.input_audio_transcription.completed":
                return event.transcript.strip(), Call(event.usage, billed_seconds(event.usage, samples), model)

            if event.type == "error" or event.type.endswith("failed"):
                raise RuntimeError(f"live transcription failed: {event}")

    raise RuntimeError("live transcription ended without a transcript")


def billed_seconds(usage, samples: np.ndarray) -> float:
    """Return the seconds a call was billed for, which the response gives rounded up."""
    return float(getattr(usage, "seconds", None) or len(samples) / speech.SAMPLE_RATE)


def transcribe_chunk(samples: np.ndarray, model: str, keywords: list[str]) -> tuple[str, list[Call]]:
    """Transcribe one run of speech, asking the live model again if the first hears nothing. Return (text, calls)."""
    response = llm.client().audio.transcriptions.create(
        model=model,
        file=("chunk.wav", wav_bytes(samples), "audio/wav"),
        extra_body={"keywords": keywords} if keywords else None,
    )
    calls = [Call(response.usage, billed_seconds(response.usage, samples), model)]

    if response.text.strip() or model == FALLBACK_MODEL:
        return response.text.strip(), calls

    text, call = transcribe_live(samples, FALLBACK_MODEL, keywords)

    return text, calls + [call]


def chunked(samples: np.ndarray, model: str, keywords: list[str]) -> tuple[list[Line], list[Call]]:
    """Find runs of speech locally, then transcribe each one in its own call."""
    chunks = speech.speech_chunks(samples)
    clips = [speech.clip(samples, start, end) for start, end in chunks]

    with ThreadPoolExecutor(PARALLEL_CALLS) as pool:
        results = list(pool.map(lambda clip: transcribe_chunk(clip, model, keywords), clips))

    lines = [Line(start, end, text) for (start, end), (text, _) in zip(chunks, results) if text]
    calls = [call for _, chunk_calls in results for call in chunk_calls]

    return lines, calls


def voice_letters(voices: list[list[float] | None], threshold: float = SAME_VOICE) -> list[str | None]:
    """Give each voiceprint a letter, the same letter for voices alike enough to be one speaker."""
    centres: list[np.ndarray] = []
    letters = []

    for voice in voices:
        if voice is None:
            letters.append(None)
            continue

        vector = np.asarray(voice)
        scores = [float(vector @ centre / np.linalg.norm(centre)) for centre in centres]
        best = int(np.argmax(scores)) if scores else -1

        if best >= 0 and scores[best] >= threshold:
            centres[best] = centres[best] + vector
        else:
            centres.append(vector.copy())
            best = len(centres) - 1

        letters.append(chr(ord("A") + best) if best < 26 else None)

    return letters


def transcribe(path: Path, model: str = TRANSCRIBE_MODEL, keywords: list[str] | None = None):
    """Transcribe one audio file into timed lines with voiceprints. Return (lines, calls)."""
    samples = speech.load_audio(path)

    if model == DIARIZE_MODEL:
        lines, calls = diarized(path, samples, model)
    else:
        lines, calls = chunked(samples, model, keywords or [])

    for line in lines:
        line.voice = speech.voice_embedding(speech.clip(samples, line.start, line.end))

    if model != DIARIZE_MODEL:
        for line, letter in zip(lines, voice_letters([line.voice for line in lines])):
            line.speaker = letter

    return lines, calls


def name_keywords(conn) -> list[str]:
    """Return the names people have been given, as hints for spelling them."""
    rows = conn.execute("select distinct name from person where name is not null and name <> ''").fetchall()

    return [name.strip() for (name,) in rows if name.strip() and "<" not in name and ">" not in name]


def log_calls(conn, calls: list[Call], step: str = "transcript") -> float:
    """Log every transcription call and return their total cost in dollars."""
    return sum(
        llm.log_cost(
            conn, step, call.model, call.usage, request={"seconds": round(call.seconds, 2)}, seconds=call.seconds
        )
        for call in calls
    )


def print_report(lines: list[tuple], seconds: float, calls: list[Call], usd: float) -> None:
    for offset, speaker, text in lines:
        print(f"  {minutes_seconds(offset):>5}  {speaker or '?'}: {text}")

    speakers = {speaker for _, speaker, _ in lines}
    audio_seconds = sum(call.seconds for call in calls)

    print(f"\n{len(lines)} lines from {len(speakers)} speakers in {seconds:.0f} s with {TRANSCRIBE_MODEL}.")
    print(f"{len(calls)} calls over {audio_seconds:.0f} s of audio. Cost: ${usd:.4f}.")


def transcribe_moment(conn, moment_id: str, redo: bool = False) -> dict | None:
    """Transcribe a moment's audio and store the lines. Return what it did, or None if there was nothing to do."""
    audio_path = DATA_DIR / "moments" / moment_id / "audio.wav"

    if storage.local_copy(audio_path) is None or (db.transcript(conn, moment_id) and not redo):
        return None

    llm.require_price(TRANSCRIBE_MODEL)
    llm.require_price(FALLBACK_MODEL)

    started = time.monotonic()
    lines, calls = transcribe(audio_path, TRANSCRIBE_MODEL, name_keywords(conn))
    seconds = time.monotonic() - started

    with conn.transaction():
        db.save_transcript(conn, moment_id, lines)
        usd = log_calls(conn, calls)

    return {"lines": db.transcript(conn, moment_id), "seconds": seconds, "calls": calls, "usd": usd}


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe a moment's audio.")
    parser.add_argument("moment_id")
    parser.add_argument("--redo", action="store_true", help="transcribe again even if a transcript exists")
    args = parser.parse_args()

    audio_path = DATA_DIR / "moments" / args.moment_id / "audio.wav"

    if storage.local_copy(audio_path) is None:
        print(f"error: no audio at {audio_path}", file=sys.stderr)
        return 1

    with db.connect() as conn:
        result = transcribe_moment(conn, args.moment_id, args.redo)

    if result is None:
        print("This moment already has a transcript. Pass --redo to transcribe it again.")
        return 0

    print_report(result["lines"], result["seconds"], result["calls"], result["usd"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
