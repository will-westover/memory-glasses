"""Turn a moment's audio into timed lines of text, each labelled with who spoke."""

import argparse
import subprocess
import sys
import time
from pathlib import Path

from memory import db, llm
from memory.config import DATA_DIR, TRANSCRIBE_MODEL, clock
from memory.ingest.recording import refresh_page

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
        raise SystemExit(f"error: {path.name} is too long to send in one request. Split the moment first.")

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
    print(f"Look at: {moment_dir / 'index.html'}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Transcribe a moment's audio.")
    parser.add_argument("moment_id")
    parser.add_argument("--redo", action="store_true", help="transcribe again even if a transcript exists")
    args = parser.parse_args()

    moment_dir = DATA_DIR / "moments" / args.moment_id
    audio_path = moment_dir / "audio.wav"

    if not audio_path.exists():
        print(f"error: no audio at {audio_path}", file=sys.stderr)
        return 1

    with db.connect() as conn:
        if db.transcript(conn, args.moment_id) and not args.redo:
            print("This moment already has a transcript. Pass --redo to transcribe it again.")
            return 0

        llm.require_price(TRANSCRIBE_MODEL)

        started = time.monotonic()
        segments, usage = transcribe(audio_path)
        seconds = time.monotonic() - started

        with conn.transaction():
            db.save_transcript(conn, args.moment_id, segments)
            usd = llm.log_cost(conn, "transcript", TRANSCRIBE_MODEL, usage)

        lines = db.transcript(conn, args.moment_id)
        refresh_page(conn, moment_dir)

    print_report(lines, seconds, usage, usd, moment_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
