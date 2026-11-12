"""Settings shared by every part of the server."""

import os
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SERVER_DIR.parent


def load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines from a .env file into the environment, without replacing values already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key and not key.startswith("#"):
            os.environ.setdefault(key, value.strip("\"'"))


load_env_file(SERVER_DIR / ".env")

# Recordings, frames and audio live here. It is personal data and is never committed.
DATA_DIR = Path(os.environ.get("MEMORY_DATA_DIR", PROJECT_ROOT / "data"))

# The default matches the local database in compose.yaml.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://memory:memory@localhost:5433/memory")

# Captions run once per kept frame, so this is the cheapest model that can read an image.
CAPTION_MODEL = os.environ.get("CAPTION_MODEL", "gpt-6-luna")


def require(name: str) -> str:
    """Return an environment variable, or stop with a message saying where to set it."""
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"error: {name} is not set. Add it to {SERVER_DIR / '.env'}")
    return value
