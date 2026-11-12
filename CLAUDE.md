# Memory Glasses

Camera glasses that record what the wearer sees and hears, and an agent that answers questions about it
("where did I leave my keys?"). CS class final project. The graded part is the LLM software: embeddings,
structured output, tool calling, and a judge model.

## How we work

- Build one part at a time, in the order in [docs/build-plan.md](docs/build-plan.md). One part per session.
- A part is done when it runs. End each part by telling the user what was built, where the files are, and the
  one command that shows it working. Then update the status in the build plan.
- Don't start the next part until the user has looked at the current one.
- Record any design decision that isn't obvious from the code in [docs/decisions.md](docs/decisions.md): what
  was chosen, what was rejected, and why.
- Keep each part small enough to read in one sitting. No code for later parts.

## Git

Kept deliberately simple. Git here is a save history, not a release process.

- Work directly on `main`. No branches, no pull requests, no CI.
- Commit whenever it's useful to save. A commit doesn't have to work.
- When a part is finished and the user has looked at it, run `uv run pytest` in `server/`, commit, and tag it
  `part-N`. Tags are the known-good points.
- To see what a part changed: `git diff part-1 part-2`. To go back to a working state: `git checkout part-2`.
- GitHub is optional and only a backup: a private repo and `git push --tags`, nothing else.

## Sample recording

`data/samples/nasa/crew-quarters-tour.webm` is the standard test clip: the first 2 minutes of NASA's "Crew
Quarters Tour Inside the Space Station" (public domain), from
[Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Crew_Quarters_Tour_Inside_the_Space_Station.webm).
It has speech and a person showing where things are. `data/` is not committed, so download it again if it's
missing.

## How data flows

Glasses capture frames and audio -> server ingest -> drop near-duplicate frames, find speech -> captions and
speech-to-text -> embeddings -> Postgres with pgvector -> agent with search tools -> judge and cost log.
The iPhone app talks to the server and pairs the glasses over Bluetooth.

Diagrams: [FigJam board](https://www.figma.com/board/io53Tnf7YwhP0FyOC8mVFf).
App screens: [Figma, page "MVP screens"](https://www.figma.com/design/trkc2YDI30hcaPkjYGVtTr).

## Languages

| Part | Language |
|---|---|
| `server/` | Python 3.12 or newer, managed with `uv` |
| Database | Postgres with pgvector, plain SQL migrations |
| `ios/` | Swift with SwiftUI |
| `firmware/glasses/` | Rust on ESP-IDF (`esp-idf-svc`), calling Espressif's C camera driver |
| `hardware/cad/` | Python scripts for Fusion |

## Layout

Code sits at the top level: `server/`, `ios/`, `firmware/`. Everything physical (CAD, print files, parts list,
wiring) is in `hardware/` and has its own [README](hardware/README.md). Fusion's Scripts folder holds links to
`hardware/cad/fusion/*`, and those scripts contain the full path `~/projects/memory-glasses/hardware/cad/`, so
moving that folder means updating both.

## Models

| Job | Model |
|---|---|
| Frame captions | `claude-haiku-5-5` |
| Agent that answers questions | `claude-opus-5-5` |
| Judge | `claude-sonnet-5-5` |

## Commands

Run these from `server/`.

```bash
uv run pytest                                          # tests
uv run python -m memory.ingest.recording <video file>  # turn a recording into a session
```

## Rules

- `data/` holds personal recordings. Never commit it or send it anywhere except the model calls the pipeline
  makes.
- Secrets go in `.env`, never in code.
- Log every model call's tokens and cost. The Stats screen and the report depend on it.
- Prefer the standard library and tools already installed (`ffmpeg`) over new dependencies.
