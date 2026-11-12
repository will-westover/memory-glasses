# Build plan

Ten parts. Each one runs by itself and ends with something to look at. Parts 1 to 8 need no hardware: they work
from a video recorded on a phone, so the graded software is finished before the glasses are.

| # | Part | What you can see when it's done | Status |
|---|---|---|---|
| 1 | Recording to session | A folder of kept frames, the audio, and a page showing them | Done |
| 2 | Database | Sessions and frames stored in Postgres; a query lists them | Not started |
| 3 | Captions | Each frame has a summary, objects and any text seen | Not started |
| 4 | Speech-to-text | The audio becomes timed transcript lines | Not started |
| 5 | Embeddings and search | A search command returns the right frames and lines | Not started |
| 6 | Agent | Ask a question in the terminal, get an answer with its sources | Not started |
| 7 | Evals | A test set is run and scored by the judge, with cost per run | Not started |
| 8 | API | The server answers the app's requests | Not started |
| 9 | iPhone app | Timeline, Ask, Stats and Glasses screens working in the simulator | Not started |
| 10 | Glasses firmware | The glasses upload frames and audio, and show answers | Not started |

## Part 1: recording to session

`server/memory/ingest/recording.py` takes a video file and writes `data/sessions/<start time>/`:

- `frames/`: one frame every 2 seconds, with near-duplicates dropped.
- `audio.wav`: mono, 16 kHz, ready for speech-to-text.
- `manifest.json`: every kept frame with its time and fingerprint.
- `index.html`: the kept frames on one page.

The glasses will later upload frames and audio directly. They will write the same session folder, so every
later part works on both.

## Needs your OK before it starts

These install or download something, so they wait for a yes.

| Part | What it needs |
|---|---|
| 2 | Postgres with pgvector, run in Docker (Docker is already installed) |
| 3 | An Anthropic API key in `server/.env` |
| 4 | A speech-to-text provider (not chosen yet) |
| 10 | The Rust toolchain for ESP32 (`rustup`, `espup`, `espflash`) |

## Part 10 in Rust: what to expect

The app code is Rust. The camera, Wi-Fi and Bluetooth drivers underneath are Espressif's C code, which Rust
calls through `esp-idf-sys`. No maintained Rust crate wraps the camera driver, so the build has to compile the
C driver as an extra component and generate bindings for it. Others have done this, but their examples are old,
so the first step of part 10 is a small test that takes one photo. If that test costs more than a day, fall
back to C++ for the camera only.

`firmware/display_test/display_test.ino` stays as the wiring bench test until part 10 replaces it.
