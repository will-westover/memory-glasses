# Memory Glasses

Camera glasses that record what you see and hear, and an agent that answers questions about it ("where did I
leave my keys?"). CS class final project. The graded part is the LLM software: embeddings, structured output,
tool calling, and a judge model.

## Layout

```
server/      Python: ingest, captions, search, agent, evals, API
ios/         Swift: the iPhone app (not started)
firmware/    Code that runs on the glasses
docs/        Build plan and design decisions
hardware/    CAD, print files, parts list, wiring and assembly
data/        Recordings and sessions. Personal, never committed
```

The software is built in ten parts. See [docs/build-plan.md](docs/build-plan.md) for the list and where it
stands, and [docs/decisions.md](docs/decisions.md) for why things are the way they are.

## Run it

From `server/`:

```bash
uv run pytest
```

```bash
uv run python -m memory.ingest.recording path/to/video.mov
```

The second command turns a recorded video into a session folder under `data/sessions/` and prints the path of
a page showing the frames it kept.

## Hardware

Building the glasses themselves is covered in [hardware/README.md](hardware/README.md), with soldering in
[hardware/WIRING.md](hardware/WIRING.md).
