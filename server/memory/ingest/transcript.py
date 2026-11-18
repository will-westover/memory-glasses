"""Turn a moment's audio into timed lines of text, each labelled with who spoke."""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from memory import db, llm, storage
from memory.config import DATA_DIR, TRANSCRIBE_MODEL, clock

UPLOAD_KBPS = 48
UPLOAD_LIMIT_BYTES = 25 * 1024 * 1024


def small_mp3(path: Path) -> bytes:
    """Return the audio as a mono MP3 small enough to upload."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-b:a", f"{UPLOAD_KBPS}k", "-f", "mp3", "-"],
        capture_output=True, check=True,
    )

    return result.stdout


def transcribe(path: Path, model: str = TRANSCRIBE_MODEL):
    """Transcribe one audio file. Return (segments, usage)."""
    audio = small_mp3(path)

    if len(audio) > UPLOAD_LIMIT_BYTES:
        raise RuntimeError(f"{path.name} is too long to send in one request. Split the moment first.")

    response = llm.client().audio.transcriptions.create(
        model=model,
        file=("audio.mp3", audio, "audio/mpeg"),
        response_format="diarized_json",
        chunking_strategy="auto",
    )

    return response.segments, response.usage


def print_report(lines: list[tuple], seconds: float, usage, usd: float, moment_dir: Path) -> None:
    for offset, speaker, text in lines:
        print(f"  {clock(offset):>5}  {speaker or '?'}: {text}")

    speakers = {speaker for _, speaker, _ in lines}

    print(f"\n{len(lines)} lines from {len(speakers)} speakers in {seconds:.0f} s with {TRANSCRIBE_MODEL}.")
    print(f"Tokens: {usage.input_tokens:,} in, {usage.output_tokens:,} out. Cost: ${usd:.4f}.")


def transcribe_moment(conn, moment_id: str, redo: bool = False) -> dict | None:
    """Transcribe a moment's audio and store the lines. Return what it did, or None if there was nothing to do."""
    moment_dir = DATA_DIR / "moments" / moment_id
    audio_path = moment_dir / "audio.wav"

    if storage.local(audio_path) is None or (db.transcript(conn, moment_id) and not redo):
        return None

    llm.require_price(TRANSCRIBE_MODEL)

    started = time.monotonic()
    segments, usage = transcribe(audio_path)
    seconds = time.monotonic() - started

    with conn.transaction():
        db.save_transcript(conn, moment_id, segments)
        usd = llm.log_cost(conn, "transcript", TRANSCRIBE_MODEL, usage)

    return {"lines": db.transcript(conn, moment_id), "seconds": seconds, "usage": usage, "usd": usd}


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe a moment's audio.")
    parser.add_argument("moment_id")
    parser.add_argument("--redo", action="store_true", help="transcribe again even if a transcript exists")
    args = parser.parse_args()

    moment_dir = DATA_DIR / "moments" / args.moment_id

    if not (moment_dir / "audio.wav").exists():
        print(f"error: no audio at {moment_dir / 'audio.wav'}", file=sys.stderr)
        return 1

    with db.connect() as conn:
        result = transcribe_moment(conn, args.moment_id, args.redo)

    if result is None:
        print("This moment already has a transcript. Pass --redo to transcribe it again.")
        return 0

    print_report(result["lines"], result["seconds"], result["usage"], result["usd"], moment_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
