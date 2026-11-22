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

Inside `server/memory/`, the same word means the same thing at every layer:

| Idea | The work | Its routes | Its tables |
| --- | --- | --- | --- |
| Moments | [pipeline.py](server/memory/pipeline.py), [ingest/](server/memory/ingest/) | [api/moments.py](server/memory/api/moments.py) | [db/moments.py](server/memory/db/moments.py), [db/facts.py](server/memory/db/facts.py) |
| Asking | [agent.py](server/memory/agent.py), [search.py](server/memory/search.py), [chat.py](server/memory/chat.py) | [api/chats.py](server/memory/api/chats.py) | [db/chats.py](server/memory/db/chats.py), [db/costs.py](server/memory/db/costs.py) |
| Glasses | [glasses.py](server/memory/glasses.py) | [api/glasses.py](server/memory/api/glasses.py) | [db/recordings.py](server/memory/db/recordings.py) |
| Slow work | [jobs.py](server/memory/jobs.py), [worker.py](server/memory/worker.py) | [api/uploads.py](server/memory/api/uploads.py) | [db/jobs.py](server/memory/db/jobs.py) |
| Places and rooms | [places.py](server/memory/places.py), [gps.py](server/memory/gps.py) | [api/places.py](server/memory/api/places.py) | [db/places.py](server/memory/db/places.py) |
| People | [people.py](server/memory/people.py), [ingest/faces.py](server/memory/ingest/faces.py) | [api/people.py](server/memory/api/people.py) | [db/people.py](server/memory/db/people.py) |
| Files | [storage.py](server/memory/storage.py), [moment_files.py](server/memory/moment_files.py) | | Cloudflare R2 |
| Evals | [evals.py](server/memory/evals.py), [evals/questions.json](server/evals/questions.json) | [api/settings.py](server/memory/api/settings.py) (stats) | |

## What happens to a photo

1. The glasses take a photo and send it over Bluetooth in pieces: [link.rs](firmware/glasses/src/link.rs), using
   the packets in [protocol](firmware/protocol/src/lib.rs).
2. The phone checks it and confirms: [GlassesLink.swift](app/MemoryGlasses/Glasses/GlassesLink.swift).
3. The phone uploads it to storage and tells the server:
   [FrameUploader.swift](app/MemoryGlasses/Glasses/FrameUploader.swift) to `POST /glasses/frames` in
   [api/glasses.py](server/memory/api/glasses.py).
4. The server counts it in the `recording` table: [glasses.py](server/memory/glasses.py).
5. When the recording ends, a worker job joins the photos into a video: `process_glasses` in
   [jobs.py](server/memory/jobs.py).
6. The pipeline keeps the sharp frames, captions them, finds faces and rooms, and writes facts with embeddings:
   [pipeline.py](server/memory/pipeline.py). The result is a moment.
7. Frames are grouped into scenes where the room changes, the photos stop looking alike, there is a gap or two
   minutes pass. One call per scene sees its photos, captions, people and speech, and records each thing seen as a
   sighting of a thing already known in the recording or a new one; people come from faces. Facts, the search
   index, are rebuilt from the scenes, things, people, text and speech: [ingest/facts.py](server/memory/ingest/facts.py),
   [db/world.py](server/memory/db/world.py).
8. The audio becomes timed lines, each with a voiceprint: [ingest/transcript.py](server/memory/ingest/transcript.py).
   With `TRANSCRIBE_MODEL=gpt-transcribe`, Silero VAD finds the runs of speech, each run is transcribed on its own
   (with `gpt-live-transcribe` when that hears nothing), and WeSpeaker gives each line a voiceprint:
   [ingest/speech.py](server/memory/ingest/speech.py). The model files go in the ignored `server/models/`:
   `silero_vad.onnx` from github.com/snakers4/silero-vad and `voxceleb_resnet34_LM.onnx` from
   huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM.

A recording made in the app skips steps 1 to 5: it uploads the video straight to storage
([MomentUpload.swift](app/MemoryGlasses/Record/MomentUpload.swift)) and the pipeline runs on it.

## What happens to a question

1. You type it in the app: [ChatView.swift](app/MemoryGlasses/Ask/ChatView.swift).
2. The app posts it and reads the answer as it streams: [ConversationStore.swift](app/MemoryGlasses/Ask/ConversationStore.swift)
   to `POST /conversations/{id}/ask/stream` in [api/chats.py](server/memory/api/chats.py).
3. The agent decides what to look up, with four tools (search, timeline, entity, nearby): [agent.py](server/memory/agent.py).
4. Search works out which time the question is about and looks there first, finds facts by meaning and by
   keyword, merges the two, and keeps one fact per thing per scene: [search.py](server/memory/search.py),
   [when.py](server/memory/when.py).
5. The agent writes the answer with `[1]` markers for its sources; the raw messages and the cost of every model call
   are saved: [db/chats.py](server/memory/db/chats.py).
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

Everything else on the server runs from `server/` as `uv run python -m memory.<module>`, for example
`memory.agent "what did the sign say?"`, `memory.search "sign"`, `memory.pipeline video.mov`,
`memory.places list`, `memory.people list`. `--help` lists each one's options.

## More

- The diagrams: [FigJam board](https://www.figma.com/board/io53Tnf7YwhP0FyOC8mVFf).
- Goals, the words used (moment, frame, segment, place) and the rules the code follows: [CLAUDE.md](CLAUDE.md).
