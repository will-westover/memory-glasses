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

# The caption is the only record of a frame, so this is the model that names things most accurately.
# Measured at about $0.0085 per frame. gpt-6-luna is about 20 times cheaper and names things more vaguely.
CAPTION_MODEL = os.environ.get("CAPTION_MODEL", "gpt-6-sol")

# Speech-to-text that also labels who is speaking and gives each line a start and end time.
# Measured at about $0.019 per minute of audio. OpenAI retires this model on 2027-02-26.
TRANSCRIBE_MODEL = os.environ.get("TRANSCRIBE_MODEL", "gpt-4o-transcribe-diarize")

# Answers questions. It makes several calls per question, so it is the cheap model; the evals will show
# whether a stronger one is worth the price.
AGENT_MODEL = os.environ.get("AGENT_MODEL", "gpt-6-luna")

# Turns text into 1536 numbers so that texts with similar meaning land close together.
# The vector columns in the database are sized for this model.
EMBED_MODEL = "text-embedding-3-small"


def require(name: str) -> str:
    """Return an environment variable, or stop with a message saying where to set it."""
    value = os.environ.get(name)

    if not value:
        raise SystemExit(f"error: {name} is not set. Add it to {SERVER_DIR / '.env'}")

    return value
