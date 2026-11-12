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
Parts and status are in [docs/build-plan.md](docs/build-plan.md).

## Constraints

- Build one part at a time, and show the user something that runs before starting the next.
- Models come from OpenAI. The key is in `server/.env`. Log the tokens and cost of every call.
- `data/` and `.env` are personal and never committed.
- Server is Python with `uv`, the app is Swift, the firmware is Rust.
- Code lives at the top level and everything physical lives in `hardware/`. Fusion's Scripts folder links
  into `hardware/cad/fusion/`, so don't move it.
- Git: work on `main`, commit freely, tag `part-N` when a part works. No branches, pull requests or CI.

Everything else is your call.

## Handy

- Tests: `uv run pytest` in `server/`.
- Test clip: `data/samples/nasa/crew-quarters-tour.webm` (public-domain NASA tour, 2 minutes, with speech).
