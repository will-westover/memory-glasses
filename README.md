# Memory Glasses

Camera glasses that record what you see and hear, and an agent that answers any question about it: what a sign
said, who you talked to, what you were doing, where something is. CS class final project.

## Where things live

| Folder | What it is |
| --- | --- |
| [server/](server/) | Python. Turns recordings into searchable facts and answers questions. Runs on Cloud Run. |
| [app/](app/) | The SwiftUI iPhone app: Ask, Timeline, Glasses, Places, People, Record and Stats. |
| [firmware/glasses/](firmware/glasses/) | Rust for the glasses (XIAO ESP32-S3 Sense): camera, display, Bluetooth, Wi-Fi. |
| [firmware/protocol/](firmware/protocol/) | The Bluetooth protocol, defined once in Rust. Generates the app's constants. |
| [hardware/](hardware/) | CAD, print files, parts and wiring. Start at [hardware/README.md](hardware/README.md). |
| [docs/](docs/) | The class overview ([PROJECT.md](docs/PROJECT.md)) and the work log ([LOG.md](docs/LOG.md)). |

Inside `server/server/`, the same word means the same thing at every layer:

| Idea | The work | Its routes | Its tables |
| --- | --- | --- | --- |
| Moments | [pipeline.py](server/server/pipeline.py), [ingest/](server/server/ingest/) | [api/moments.py](server/server/api/moments.py) | [db/moments.py](server/server/db/moments.py), [db/facts.py](server/server/db/facts.py) |
| Asking | [agent/](server/server/agent/), [search.py](server/server/search.py), [chat.py](server/server/chat.py) | [api/chats.py](server/server/api/chats.py) | [db/chats.py](server/server/db/chats.py), [db/costs.py](server/server/db/costs.py) |
| Glasses | [glasses.py](server/server/glasses.py) | [api/glasses.py](server/server/api/glasses.py) | [db/recordings.py](server/server/db/recordings.py) |
| Slow work | [jobs.py](server/server/jobs.py), [worker.py](server/server/worker.py) | [api/uploads.py](server/server/api/uploads.py) | [db/jobs.py](server/server/db/jobs.py) |
| Places and rooms | [places.py](server/server/places.py), [gps.py](server/server/gps.py) | [api/places.py](server/server/api/places.py) | [db/places.py](server/server/db/places.py) |
| People | [people.py](server/server/people.py), [ingest/faces.py](server/server/ingest/faces.py) | [api/people.py](server/server/api/people.py) | [db/people.py](server/server/db/people.py) |
| Files | [storage.py](server/server/storage.py), [moment_files.py](server/server/moment_files.py) | | Cloudflare R2 |
| Evals | [evals.py](server/server/evals.py), [evals/questions.json](server/evals/questions.json) | [api/settings.py](server/server/api/settings.py) (stats) | |

## What happens to a photo

1. The glasses take a photo and send it over Bluetooth in pieces: [link.rs](firmware/glasses/src/link.rs), using
   the packets in [protocol](firmware/protocol/src/lib.rs).
2. The phone checks it and confirms: [GlassesLink.swift](app/MemoryGlasses/Glasses/GlassesLink.swift).
3. The phone uploads it to storage and tells the server:
   [FrameUploader.swift](app/MemoryGlasses/Glasses/FrameUploader.swift) to `POST /glasses/frames` in
   [api/glasses.py](server/server/api/glasses.py).
4. The server counts it in the `recording` table: [glasses.py](server/server/glasses.py).
5. When the recording ends, a worker job joins the photos into a video: `process_glasses` in
   [jobs.py](server/server/jobs.py).
6. The pipeline keeps the sharp frames, each with a small copy for lists ([thumbs.py](server/server/thumbs.py)), transcribes the speech, captions each frame (with the words spoken around
   it), finds faces and rooms, and builds the world model: [pipeline.py](server/server/pipeline.py). The result is a
   moment.
7. The audio becomes timed lines, each with a voiceprint: [ingest/transcript.py](server/server/ingest/transcript.py).
   With `TRANSCRIBE_MODEL=gpt-transcribe`, Silero VAD finds the runs of speech, each run is transcribed on its own
   (with `gpt-live-transcribe` when that hears nothing), and WeSpeaker gives each line a voiceprint:
   [ingest/speech.py](server/server/ingest/speech.py). The model files go in the ignored `server/models/`:
   `silero_vad.onnx` from github.com/snakers4/silero-vad and `voxceleb_resnet34_LM.onnx` from
   huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM.
8. Frames are grouped into scenes where the room changes, the photos stop looking alike, there is a gap or two
   minutes pass. One call per scene sees its photos, captions, people and speech, and records each thing seen as a
   sighting of a thing already known in the recording or a new one; people come from faces:
   [ingest/facts.py](server/server/ingest/facts.py), [db/world.py](server/server/db/world.py).
9. Each new thing is compared with the closest-reading earlier things, those at the same place first, by a vision
   check of the two photo crops. It links only when certain and no other candidate could be it; a link can be undone
   (`/things/links`), and "not the same" is remembered: [ingest/link.py](server/server/ingest/link.py).
10. One call groups the scenes into activities and the speech into whole conversations, and the day's summary is
    rewritten from its activities: [ingest/activities.py](server/server/ingest/activities.py).
11. Facts, the search index, are rebuilt from all of this: one per recording, activity, conversation, day, scene,
    thing, person, text and passage, each pointing at its photos.

A recording made in the app skips steps 1 to 5: it uploads the video straight to storage
([MomentUpload.swift](app/MemoryGlasses/Record/MomentUpload.swift)) and the pipeline runs on it.

## What happens to a question

1. You type it in the app: [ChatView.swift](app/MemoryGlasses/Ask/ChatView.swift).
2. The app posts it and reads the answer as it streams: [ConversationStore.swift](app/MemoryGlasses/Ask/ConversationStore.swift)
   to `POST /conversations/{id}/ask/stream` in [api/chats.py](server/server/api/chats.py).
3. The question is searched straight away: its time is worked out while it is embedded, then search looks in that
   time first, finds facts by meaning and by keyword, merges the two, and keeps one fact per thing per scene:
   [search.py](server/server/search.py), [when.py](server/server/when.py).
4. The agent answers from those facts, or calls tools (search, timeline, coverage, entity, nearby) until it can;
   every fact it reads names its recording. The prompt is in [agent/prompt.py](server/server/agent/prompt.py), the
   tools in [agent/tools.py](server/server/agent/tools.py) and the loop in [agent/loop.py](server/server/agent/loop.py).
5. The agent writes the answer with `[1]` markers for its sources; the raw messages and the cost of every model call
   are saved: [db/chats.py](server/server/db/chats.py).
6. Tapping a source opens the Timeline at that photo: [MomentView.swift](app/MemoryGlasses/Timeline/MomentView.swift).

## Commands

Run `make` to list them.

| Command | What it does |
| --- | --- |
| `make test` | Run the server tests |
| `make evals` | Ask every test question and grade the answers |
| `make moments` | List the recorded moments |
| `make app` | Build the iPhone app for the Simulator |
| `make protocol` | Test the Bluetooth protocol and check the app against it |
| `make firmware` | Build the glasses firmware |
| `make flash` | Build the firmware and flash it over USB |
| `make publish` | Publish the firmware so the glasses update themselves |
| `make deploy` | Build the server and deploy it to Cloud Run |

Everything else on the server runs from `server/` as `uv run python -m server.<module>`, for example
`server.agent "what did the sign say?"`, `server.search "sign"`, `server.pipeline video.mov`,
`server.places list`, `server.people list`. `--help` lists each one's options.

## More

- The diagrams: [FigJam board](https://www.figma.com/board/io53Tnf7YwhP0FyOC8mVFf).
- Goals, the words used (moment, frame, segment, place) and the rules the code follows: [CLAUDE.md](CLAUDE.md).
