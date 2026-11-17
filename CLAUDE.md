# Memory Glasses

Camera glasses that record what the wearer sees and hears, and an agent that answers any question about it:
what a sign said, who they talked to, what they were doing, where something is. CS class final project. The
graded part is the LLM software: embeddings, structured output, tool calling, and a judge model.

## Goals

- A question gets a correct answer with its sources, or an honest "I don't know".
- Evals show how often it's right and what each answer costs.
- Small enough to explain in a class report.

## Shape

Recording -> kept frames and audio -> captions and transcript -> small facts, each embedded with its context,
in Postgres (pgvector) -> agent with search tools -> judge and cost log. An iPhone app sits on top, and the glasses firmware comes last.

Words: a **moment** is one recording, start to finish; a **frame** is one photo kept from it.

## Build plan

Each part runs by itself and ends with something to look at. Parts 1 to 8 work from a recorded video.

1. **Recording to moment** (done): a video becomes kept frames, audio and a page showing them.
2. **Database** (done): moments and frames stored in Postgres.
3. **Captions** (done): each frame gets a summary, objects with where each one is, and text seen.
4. **Speech-to-text** (done): the audio becomes timed transcript lines.
5. **Facts and search** (done): captions and speech become facts, plus model-written summaries of each
   minute and of what was said, and search finds them.
6. **Agent** (done): ask a question, get an answer with its sources. The agent has two tools, search and
   timeline; `--plain` is one search and one call, the baseline the evals compare it with.
7. **Evals** (done): `memory.evals` asks every test question in both modes; a judge model grades each
   answer, and the run reports scores, whether the right fact was retrieved, and cost.
8. **API** (done): `memory/api.py` (FastAPI) serves moments, a moment's timeline, frame images, ask,
   stats and settings.
9. **iPhone app** (done): SwiftUI in `app/`, with Ask (chats saved on the server, with follow-ups), Timeline,
   Stats and Glasses screens. Tapping a source opens its frame: the photo, nearby frames, what was said, audio. Build and run
   it in the iOS Simulator with the server running on port 8000.
10. **Glasses firmware**: the glasses upload frames and audio. Start with a test that takes one photo, since
    Rust has to call Espressif's C camera driver.

## Constraints

- These are general-purpose memory glasses. Don't slant prompts, code, examples or docs toward finding lost
  items; that is one kind of question among many.
- Build one part at a time, and show the user something that runs before starting the next.
- Models come from OpenAI. The key is in `server/.env`. Log the tokens and cost of every call.
- `data/` and `.env` are personal and never committed.
- Server is Python with `uv`, the app is Swift, the firmware is Rust.
- Code lives at the top level and everything physical lives in `hardware/`. Fusion's Scripts folder links
  into `hardware/cad/fusion/`, so don't move it.
- Inside functions, put a blank line between groups of lines that do different jobs.
- No comments in code. Docstrings are one short line.
- Avoid fixed lists and hard categories for what the model describes; let it use its own words.
- Tests only for tricky logic. The evals are the test for anything that calls a model.
- Git: work on `main`, commit freely, tag `part-N` when a part works. No branches, pull requests or CI.

Everything else is your call.

## Handy

- Tests: `uv run pytest` in `server/`.
- Every step runs from `server/` as `uv run python -m <module>`; `--help` lists its options.
- Process a video in one go: `memory.pipeline <video> [--frames-per-minute N]` (frames, captions, transcript,
  faces, facts). The single steps still run on their own: `memory.ingest.recording`, `.captions`, `.transcript`,
  `.faces`, `.facts`. The original video is kept in the moment folder. Then `memory.agent "question"` (add
  `--plain` for the baseline), or `memory.search "question"` to see the raw facts.
- The app records moments on an iPhone through ARKit and uploads them (`POST /moments`); they process in the
  background. Recording needs a real iPhone; the Simulator has no camera tracking.
- People: `memory.people list`, `name <id> "Sam"`, `merge <id> <into id>`, `forget <id>`, or the People screen in
  the app. Faces use OpenCV's YuNet and SFace models in the ignored `server/models/` (from github.com/opencv/opencv_zoo).
  The user has written consent from the people recorded.
- Places: `memory.places add "Home" <lat> <lon> --radius 75`, `memory.places list`.
- Model prices are in `server/prices.json`. A model with no price there is refused before any call.
- Database: `docker compose up -d --wait` in `server/`, then `uv run python -m memory.db list`. New tables go
  in a new numbered file in `server/migrations/`.
- Test clips, both in the ignored `data/samples/`: `nasa/crew-quarters-tour.webm` (public-domain NASA tour,
  2 minutes, with speech) and `home/IMG_0161.MOV` (the user's own 12-second walk through their home, with
  location). The user's recordings are real data on their own account; don't add test users or fake data.
