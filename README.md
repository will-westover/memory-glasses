# Memory Glasses

Camera glasses that record what you see and hear, and an agent that answers questions about it ("where did I
leave my keys?"). CS class final project.

- `server/`: the software. Run `uv run pytest` there to test it.
- `firmware/`: code that runs on the glasses.
- `hardware/`: CAD, print files, parts list and wiring. Start at [hardware/README.md](hardware/README.md).
- `docs/build-plan.md`: what's built and what's next.

Try it on a video, from `server/`:

```bash
uv run python -m memory.ingest.recording path/to/video.mov
```
