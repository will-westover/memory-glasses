"""Turn a session's audio into timed lines of text, each labelled with who spoke.

    uv run python -m memory.ingest.transcript <session id>
    uv run python -m memory.ingest.transcript <session id> --redo
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from memory import db, llm
from memory.config import DATA_DIR, TRANSCRIBE_MODEL
from memory.ingest.recording import clock, refresh_page

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
        raise SystemExit(f"error: {path.name} is too long to send in one request. Split the session first.")

    response = llm.client().audio.transcriptions.create(
        model=model,
        file=("audio.mp3", audio, "audio/mpeg"),
        response_format="diarized_json",
        chunking_strategy="auto",
    )

    return response.segments, response.usage


def print_report(lines: list[tuple], seconds: float, usage, usd: float, session_dir: Path) -> None:
    for offset, speaker, text in lines:
        print(f"  {clock(offset):>5}  {speaker or '?'}: {text}")

    speakers = {speaker for _, speaker, _ in lines}

    print(f"\n{len(lines)} lines from {len(speakers)} speakers in {seconds:.0f} s with {TRANSCRIBE_MODEL}.")
    print(f"Tokens: {usage.input_tokens:,} in, {usage.output_tokens:,} out. Cost: ${usd:.4f}.")
    print(f"Look at: {session_dir / 'index.html'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe a session's audio.")
    parser.add_argument("session_id")
    parser.add_argument("--redo", action="store_true", help="transcribe again even if a transcript exists")
    args = parser.parse_args()

    session_dir = DATA_DIR / "sessions" / args.session_id
    audio_path = session_dir / "audio.wav"

    if not audio_path.exists():
        print(f"error: no audio at {audio_path}", file=sys.stderr)
        return 1

    with db.connect() as conn:
        if db.transcript(conn, args.session_id) and not args.redo:
            print("This session already has a transcript. Pass --redo to transcribe it again.")
            return 0

        started = time.monotonic()
        segments, usage = transcribe(audio_path)
        seconds = time.monotonic() - started

        with conn.transaction():
            db.save_transcript(conn, args.session_id, segments)
            usd = llm.log_cost(conn, "transcript", TRANSCRIBE_MODEL, usage)

        lines = db.transcript(conn, args.session_id)
        refresh_page(conn, session_dir)

    print_report(lines, seconds, usage, usd, session_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
