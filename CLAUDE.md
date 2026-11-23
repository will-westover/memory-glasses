# Memory Glasses

Camera glasses that record what the wearer sees and hears, and an agent that answers any question about it:
what a sign said, who they talked to, what they were doing, where something is. CS class final project.

## Goals

- Create really robust and cool glasses / phone app that answers questions about your day

## Shape

Recording -> kept frames and audio -> captions and transcript -> small facts, each embedded with its context,
in Postgres (pgvector) -> agent with search tools -> judge and cost log. An iPhone app sits on top, and the glasses
send it photos.

Words: a **moment** is one recording, start to finish; a **frame** is one photo kept from it, and its **caption** is
what the model wrote about it; a **stretch** is a run of frames that are one scene, and its **summary** is what the
model wrote about it, titled as a **chapter**; a **fact** is one searchable line with a start, an end and the frames it
covers; an **entity** is one thing or person, and each time it is seen is a **sighting**; a **scene** is stored as
an **episode**; scenes are grouped into **activities**, and activities into a **day**; a **segment** is the part of a moment at one **place**, or on the move; a **room** is a part of a place. Chats are stored as raw
**messages**; every model call is a **model_call**, linked to the message or frame it was for.

Where things live, what happens to a photo and to a question, and the commands (`make`) are in `README.md`. Keep
it true when files move.

## Build plan

Each part runs by itself and ends with something to look at.

1. **Recording to moment** (done): a video becomes kept frames and audio.
2. **Database** (done): moments and frames stored in Postgres.
3. **Captions** (done): each frame gets a summary, objects with where each one is, and text seen.
4. **Speech-to-text** (done): the audio becomes timed transcript lines.
5. **Facts and search** (done): captions and speech become facts and summaries, and search finds them.
6. **Agent** (done): an answer with its sources, from three tools (search, timeline, nearby). `--plain` is one
   search and one call, the baseline the evals compare it with.
7. **Evals** (done): every test question in each mode, graded by a judge model, scored per kind of question.
8. **API** (done): FastAPI in `server/api/`, one file per area. Answers stream as server-sent events.
9. **iPhone app** (done): Ask, Timeline, Glasses, Places, People, Record and Stats.
10. **Glasses firmware** (photos work, audio next): photos go to the phone over Bluetooth, with Wi-Fi as the
    fallback; firmware updates arrive over Bluetooth. They run on USB-C power; the battery isn't working yet.

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

## Good to know

- There is no local server mode. The server runs on Cloud Run (project `memory-glasses-2611`, `us-west1`), the
  database is Neon and files are in Cloudflare R2; `data/` is a working copy that fills itself as files are needed.
  The Mac runs the command-line steps against the same database and bucket, so they act on real data.
- The server runs in the owner's time zone (`TIME_ZONE` in `server/deploy/deploy.sh`, Mountain by default), so answers
  give local times.
- It is meant to cost $0 apart from OpenAI. Model prices are in `server/prices.json`; a model with no price there
  is refused before any call.
- New tables go in a new numbered file in `server/migrations/`; they are applied on the next connection.
- Few bytes pass through the server: file requests redirect to short-lived R2 links, the app and the glasses
  upload straight to R2, and captions give OpenAI an R2 link.
- A glasses recording ends only when the phone or glasses say so (`POST /glasses/recordings/{id}/end`); nothing
  checks for ones that went quiet. The first photo of a newer recording also ends any older one still open. Photos that
  arrive later start a new part. Photos are filed by when they were taken, not when they arrived. When it is
  processed, a recording is split into separate moments wherever no photo came for 10 minutes.
- Each scene is told the things seen earlier in the same rooms and in the scene just before, not the whole
  recording, so long recordings stay fast.
- Facts cover a stretch of a moment (`fact.end_offset_seconds`, and `fact_frame` for the photos), split by code where
  the room changes, the photos stop looking alike, there is a 30 s gap or two minutes pass.
- A thing seen again at the same place is linked to the earlier one (`entity.merged_into`), never moved, so a link can
  be undone. Rebuilding a moment's facts makes its things again and links them afresh.
- While the glasses are connected, the phone sends its location (place-level, and once a minute while still) to
  `phone_location`; a recording with no location of its own takes the points from while it ran.
- A moment is filed at the place the phone's map recognised, else the place whose frames look most alike (average
  embedding at least 0.75); for now each moment is one segment. Rooms come from one model call per moment, then a
  vote of frames within 1.5 m.
- Renaming a person, room or place swaps the name in the stored text and embeds what changed again
  (`server/server/names.py`); only naming someone or somewhere for the first time rebuilds facts.
- A moment can be reprocessed at another frames per minute from its kept video; frames are named by their time,
  so ones that already exist keep their captions.
- Recording in the app needs a real iPhone; the Simulator has no camera tracking.
- The Bluetooth protocol is defined once, in `firmware/protocol`. Change it there and run `make protocol`; never
  edit the generated `GlassesProtocol.swift`. Phone and glasses prove to each other that they hold the server token
  before photos, settings or updates pass.
- Glasses Wi-Fi name, password, server and token go in the ignored `firmware/glasses/glasses.toml`; the app's
  token goes in the ignored `app/Local.xcconfig`. Install a new app before publishing firmware that changes the
  protocol.
- Evals: each question has a kind and its file names the moments it is about. Personal questions live in the
  ignored `data/evals/`.
- Faces use OpenCV's YuNet and SFace models in the ignored `server/models/`. The user has written consent from
  the people recorded.
- Two local models are the exceptions to "models come from OpenAI": faces (above) and voices. Silero VAD and
  WeSpeaker run on the CPU from `server/models/`, to split speech and to name speakers by their voiceprint.
- Test clips are in the ignored `data/samples/`. The user's recordings are real data on their own account; don't
  add test users or fake data.
