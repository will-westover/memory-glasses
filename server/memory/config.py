"""Settings and small helpers shared by every part of the server."""

import os
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SERVER_DIR.parent


def load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines from a .env file into the environment."""
    if not path.exists():
        return

    for line in path.read_text().splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key and not key.startswith("#"):
            os.environ.setdefault(key, value.strip("\"'"))


load_env_file(SERVER_DIR / ".env")

DATA_DIR = Path(os.environ.get("MEMORY_DATA_DIR", PROJECT_ROOT / "data"))

FRAMES_PER_MINUTE = float(os.environ.get("FRAMES_PER_MINUTE", 30))

CAPTION_MODEL = os.environ.get("CAPTION_MODEL", "gpt-6-sol")

TRANSCRIBE_MODEL = os.environ.get("TRANSCRIBE_MODEL", "gpt-4o-transcribe-diarize")

AGENT_MODEL = os.environ.get("AGENT_MODEL", "gpt-6-luna")

SUMMARY_MODEL = os.environ.get("SUMMARY_MODEL", "gpt-6-luna")

JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "gpt-6-sol")

STRETCH_SECONDS = 60

EMBED_MODEL = "text-embedding-3-small"


def clock(seconds: float) -> str:
    """Format seconds as m:ss."""
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def require(name: str) -> str:
    """Return an environment variable, or stop if it is not set."""
    value = os.environ.get(name)

    if not value:
        raise SystemExit(f"error: {name} is not set. Add it to {SERVER_DIR / '.env'}")

    return value
