# Memory Glasses

Camera glasses that record what the wearer sees and hears, and an agent that answers questions about it
("where did I leave my keys?"). CS class final project. The graded part is the LLM software: embeddings,
structured output, tool calling, and a judge model.

## Goals

- A question gets a correct answer with its sources, or an honest "I don't know".
- Evals show how often it's right and what each answer costs.
- Small enough to explain in a class report.

## Shape

Recording -> kept frames and audio -> captions and transcript -> embeddings in Postgres (pgvector) -> agent
with search tools -> judge and cost log. An iPhone app sits on top, and the glasses firmware comes last.

## Build plan

Each part runs by itself and ends with something to look at. Parts 1 to 8 work from a recorded video.

1. **Recording to session** (done): a video becomes kept frames, audio and a page showing them.
2. **Database** (done): sessions and frames stored in Postgres.
3. **Captions**: each frame gets a summary, objects and any text seen.
4. **Speech-to-text**: the audio becomes timed transcript lines.
5. **Embeddings and search**: a search returns the right frames and lines.
6. **Agent**: ask a question, get an answer with its sources.
7. **Evals**: a test set scored by the judge, with cost per run.
8. **API**: the server answers the app's requests.
9. **iPhone app**: Timeline, Ask, Stats and Glasses screens.
10. **Glasses firmware**: the glasses upload frames and audio. Start with a test that takes one photo, since
    Rust has to call Espressif's C camera driver.

## Constraints

- Build one part at a time, and show the user something that runs before starting the next.
- Models come from OpenAI. The key is in `server/.env`. Log the tokens and cost of every call.
- `data/` and `.env` are personal and never committed.
- Server is Python with `uv`, the app is Swift, the firmware is Rust.
- Code lives at the top level and everything physical lives in `hardware/`. Fusion's Scripts folder links
  into `hardware/cad/fusion/`, so don't move it.
- Tests only for tricky logic. The evals are the test for anything that calls a model.
- Git: work on `main`, commit freely, tag `part-N` when a part works. No branches, pull requests or CI.

Everything else is your call.

## Handy

- Tests: `uv run pytest` in `server/`.
- Database: `docker compose up -d --wait` in `server/`, then `uv run python -m memory.db list`. New tables go
  in a new numbered file in `server/migrations/`.
- Test clip: `data/samples/nasa/crew-quarters-tour.webm` (public-domain NASA tour, 2 minutes, with speech).
