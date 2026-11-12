# Build plan

Each part runs by itself and ends with something to look at. Parts 1 to 8 work from a recorded video, so the
graded software doesn't wait on the glasses.

1. **Recording to session** (done): a video becomes kept frames, audio and a page showing them.
2. **Database**: sessions and frames stored in Postgres.
3. **Captions**: each frame gets a summary, objects and any text seen.
4. **Speech-to-text**: the audio becomes timed transcript lines.
5. **Embeddings and search**: a search returns the right frames and lines.
6. **Agent**: ask a question, get an answer with its sources.
7. **Evals**: a test set scored by the judge, with cost per run.
8. **API**: the server answers the app's requests.
9. **iPhone app**: Timeline, Ask, Stats and Glasses screens.
10. **Glasses firmware**: the glasses upload frames and audio. Start with a test that takes one photo, since
    Rust has to call Espressif's C camera driver.
