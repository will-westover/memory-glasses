# Memory Glasses

Camera glasses that record what you see and hear, and an agent that answers any question about it: what a sign
said, who you talked to, what you were doing, where something is. CS class final project.

- `server/`: the software. Run `uv run pytest` there to test it.
- `firmware/`: code that runs on the glasses.
- `hardware/`: CAD, print files, parts list and wiring. Start at [hardware/README.md](hardware/README.md).
- `CLAUDE.md`: goals, constraints and the build plan, with what's done.

Try it on a video, from `server/`:

```bash
uv run python -m memory.ingest.recording path/to/video.mov
```
