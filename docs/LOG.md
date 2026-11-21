# Project log

Hours are estimated from when work was committed each day.

| Date | What | Hours |
| --- | --- | --- |
| 11/11/2026 | Set up the project. Built recording to moment, the Postgres database, frame captions, speech-to-text, and facts with embeddings and search | 3 |
| 11/16/2026 | Built the agent with search and timeline tools, evals with a judge model, and the API. Wrote the iPhone app (Ask, Timeline, Stats, Glasses), saved chats and face matching. iPhone recording with positions, the lidar room in 3D, and recognising rooms across moments | 8 |
| 11/17/2026 | Paired the phone over Tailscale. Soldered the board; camera and display tests in Rust. Glasses send photos over the iPhone hotspot and become moments; display size setting | 9 |
| 11/18/2026 | Display setup in the app (focus pattern, size, position, mirror), recording switch. Project overview. Reworked the data model in FigJam (places, segments, raw chat messages) and designed a new way to answer questions. Moved hosting to Neon, Cloudflare R2 and Cloud Run. Chat and Timeline redesign. Glasses send photos over Bluetooth and update their firmware over it | 9 |
| 11/20/2026 | Code review fixes for late and misdated photos and stuck updates. Removed the local mode, moved recordings into the database, one Bluetooth protocol in Rust, eval questions by kind (42 questions). Reorganised the repo and rewrote the README | 3 |
| **Total** | | **32** |
