# Memory Glasses

Camera glasses that record what the wearer sees and hears, and an agent that answers any question about it:
what a sign said, who they talked to, what they were doing, where something is. CS class final project.

## Goals

- Create really robust and cool glasses / phone app that answers questions about your day

## Shape

Recording -> kept frames and audio -> captions and transcript -> small facts, each embedded with its context,
in Postgres (pgvector) -> agent with search tools -> judge and cost log. An iPhone app sits on top, and the glasses firmware comes last.

Words: a **moment** is one recording, start to finish; a **frame** is one photo kept from it; a **segment** is a
stretch of a moment at one **place**, or on the move. Chats are stored as raw **messages**; every model call is a
**model_call**, linked to the message or frame it was for.

## Build plan

Each part runs by itself and ends with something to look at. Parts 1 to 8 work from a recorded video.

1. **Recording to moment** (done): a video becomes kept frames and audio.
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
   stats and settings. `POST /conversations/{id}/ask/stream` sends each step, the answer as it is written (with
   [1] markers for its sources) and then the stored turn, as server-sent events. Chats get a title from a cheap
   model; `/suggestions` writes questions from the newest records.
9. **iPhone app** (done): SwiftUI in `app/`, with Ask (chats saved on the server, with follow-ups), Timeline,
   Stats and Glasses screens. Tapping a source opens its frame: the photo, nearby frames, what was said, audio. Build and run
   it in the iOS Simulator with the server running on port 8000.
10. **Glasses firmware** (photos work, audio next): `firmware/glasses` builds `camera_test`, `display_test` and
    `glasses`. The glasses join the iPhone's hotspot and send a photo at the frames-per-minute setting to
    `POST /glasses/frames`; frames that stop for a minute become a moment. They run on the iPhone's USB-C power; the
    battery isn't working yet.

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
  background. Recording needs a real iPhone; the Simulator has no camera tracking. With a recording come
  `poses.json` (where the phone was, 10 times a second), and on Pro phones `mesh.obj` (the lidar scan) and
  `world.map` (ARKit's map, used to recognise the place next time).
- A **place** is somewhere such as an apartment or a lecture hall, with an optional name, GPS circle and phone map. A
  moment is filed at the place the phone's map recognised, else the place whose frames look most alike (average
  embedding at least 0.75); for now each moment is one segment. GPS points are kept over time in `location`. Each
  frame gets a **room** from one model call per moment that sees every frame, its position, the speech and the scan,
  then a vote of frames within 1.5 m. `memory.rooms list`, `label <moment id>`, `rebuild`. Maps are in `data/places/`.
- A moment can be reprocessed at another frames per minute from its kept video; frames are named by their time,
  so ones that already exist keep their captions.
- The agent has three tools: search, timeline and nearby (what was seen near a frame, in the same mapped place).
  `memory.evals --mode agent --mode no-nearby` compares it with and without nearby.
- People: `memory.people list`, `name <id> "Sam"`, `merge <id> <into id>`, `forget <id>`, or the People screen in
  the app. Faces use OpenCV's YuNet and SFace models in the ignored `server/models/` (from github.com/opencv/opencv_zoo).
  The user has written consent from the people recorded.
- Named GPS places: `memory.places add "Home" <lat> <lon> --radius 75`, `memory.places list`.
- Model prices are in `server/prices.json`. A model with no price there is refused before any call.
- Phone connection: the server runs with `API_TOKEN` and `--host 0.0.0.0`; Mac and phone both join Tailscale (campus
  Wi-Fi blocks devices reaching each other). `memory.pair` shows a QR code; the iPhone camera opens it in the app.
- Glasses connection: Tailscale Funnel (`tailscale funnel --bg 8000`) gives the server a public HTTPS address, since the
  board can't run Tailscale. Wi-Fi name, password, server and token go in the ignored `firmware/glasses/glasses.toml`
  (see `glasses.example.toml`). The board needs its antenna attached and a 2.4 GHz network: turn on the hotspot's
  Maximize Compatibility.
- Glasses over Bluetooth (in progress): the firmware advertises "Memory Glasses" and streams frames, takes settings and
  firmware updates over BLE (service 6e1f0001-…); with no phone for 30 s it falls back to Wi-Fi. Phone and glasses prove to each other
  that they hold the server token before photos, settings or updates pass. `firmware/glasses/flash.sh`
  flashes over USB with the two-slot OTA partition table; `publish.sh` uploads the app image and `firmware/latest.json` to R2.
- Database: `docker compose up -d --wait` in `server/`, then `uv run python -m memory.db list`. New tables go
  in a new numbered file in `server/migrations/`.
- Hosting: with `DATABASE_URL` set in `server/.env`, the database is Neon (`memory-glasses` org). With
  `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` and `R2_BUCKET` set, files live in Cloudflare R2 and `data/`
  is a working copy that fills itself as files are needed (`memory.storage`). `memory.storage sync` uploads what's
  missing; `check` compares the two. Without them, everything stays local. With R2, file requests redirect to
  short-lived R2 links, the app uploads recordings straight to R2 (`POST /uploads`), the glasses PUT each frame to an R2
  link from the previous reply and then tell the server (`X-Uploaded`), and captions give OpenAI an R2 link, so few
  bytes pass through the server.
- Cloud Run (project `memory-glasses-2611`, `us-west1`), meant to cost $0: `server/deploy/secrets.sh` copies the
  secrets from `.env` into Secret Manager, then `R2_ACCOUNT_ID=… server/deploy/deploy.sh` builds the image here,
  deploys the `memory-server` service and the `memory-worker` job (slow work: `memory.worker <job id>`), and has Cloud
  Scheduler call `POST /glasses/check` every minute. Locally, the same work runs in threads.
- Test clips, both in the ignored `data/samples/`: `nasa/crew-quarters-tour.webm` (public-domain NASA tour,
  2 minutes, with speech) and `home/IMG_0161.MOV` (the user's own 12-second walk through their home, with
  location). The user's recordings are real data on their own account; don't add test users or fake data.
