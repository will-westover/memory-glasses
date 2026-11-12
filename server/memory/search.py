"""Find the frames and transcript lines that best match a question.

    uv run python -m memory.search "where does he sleep"

Two searches run and their results are combined:
  by meaning   the question is embedded and compared with every stored embedding
  by keyword   the question's words are matched against the stored text
A result that ranks well in both ends up on top.
"""

import argparse
import sys

from memory import db, llm

# How many results each of the two searches contributes before they are combined.
CANDIDATES = 20

# Standard constant for reciprocal rank fusion. Larger values flatten the gap between ranks.
FUSION_K = 60

BY_MEANING = """
    select 'frame' as kind, id, embedding <=> %(vector)s::vector as distance
      from frame where embedding is not null
    union all
    select 'speech', id, embedding <=> %(vector)s::vector
      from audio_segment where embedding is not null
    order by distance
    limit %(limit)s
"""

# plainto_tsquery joins the words with AND; swapping in OR lets a result match on any of them.
BY_KEYWORD = """
    with words as (select replace(plainto_tsquery('english', %(text)s)::text, '&', '|')::tsquery as query)
    select 'frame' as kind, id, ts_rank(text_search, words.query) as rank
      from frame, words where text_search @@ words.query
    union all
    select 'speech', id, ts_rank(text_search, words.query)
      from audio_segment, words where text_search @@ words.query
    order by rank desc
    limit %(limit)s
"""

FRAME_DETAILS = """
    select f.id, f.session_id, extract(epoch from f.captured_at - s.started_at), f.summary, f.image_path
      from frame f join session s on s.id = f.session_id
     where f.id = any(%s)
"""

SPEECH_DETAILS = """
    select id, session_id, offset_seconds, text, speaker
      from audio_segment
     where id = any(%s)
"""


def fuse(rankings: list[list], k: int = FUSION_K) -> list[tuple]:
    """Combine several ranked lists into one, best first, as (item, score).

    Reciprocal rank fusion: an item scores 1 / (k + its position) in each list it appears in, and the scores add
    up. It only uses positions, so lists scored on different scales (distance, keyword rank) can be combined.
    """
    scores: dict = {}

    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)

    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def search(conn, question: str, limit: int = 5) -> list[dict]:
    """Return the best-matching frames and transcript lines for a question, best first."""
    vector = llm.embed(conn, [question], "embed-question")[0]

    by_meaning = conn.execute(BY_MEANING, {"vector": vector, "limit": CANDIDATES}).fetchall()
    by_keyword = conn.execute(BY_KEYWORD, {"text": question, "limit": CANDIDATES}).fetchall()

    ranked = fuse([
        [(kind, row_id) for kind, row_id, _ in by_meaning],
        [(kind, row_id) for kind, row_id, _ in by_keyword],
    ])[:limit]

    frame_ids = [row_id for (kind, row_id), _ in ranked if kind == "frame"]
    speech_ids = [row_id for (kind, row_id), _ in ranked if kind == "speech"]
    details = {}

    for row_id, session_id, offset, text, image_path in conn.execute(FRAME_DETAILS, (frame_ids,)):
        details["frame", row_id] = {"session_id": session_id, "offset_seconds": float(offset), "text": text,
                                    "image_path": image_path, "speaker": None}

    for row_id, session_id, offset, text, speaker in conn.execute(SPEECH_DETAILS, (speech_ids,)):
        details["speech", row_id] = {"session_id": session_id, "offset_seconds": float(offset), "text": text,
                                     "image_path": None, "speaker": speaker}

    meaning_hits = {(kind, row_id) for kind, row_id, _ in by_meaning}
    keyword_hits = {(kind, row_id) for kind, row_id, _ in by_keyword}

    return [
        {
            "kind": kind,
            "id": row_id,
            "score": score,
            "matched": [name for name, hits in (("meaning", meaning_hits), ("keyword", keyword_hits)) if (kind, row_id) in hits],
            **details[kind, row_id],
        }
        for (kind, row_id), score in ranked
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Search frames and transcript lines.")
    parser.add_argument("question")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    with db.connect() as conn:
        results = search(conn, args.question, args.limit)

    if not results:
        print("Nothing to search yet. Run the captions, transcript and embeddings steps first.")
        return 0

    for position, result in enumerate(results, start=1):
        offset = int(result["offset_seconds"])
        when = f"{offset // 60}:{offset % 60:02d}"
        matched = " + ".join(result["matched"])
        who = f"{result['speaker']}: " if result["speaker"] else ""

        print(f"{position}. {result['kind']:<6} {when:>5}  [{matched}]  {who}{result['text']}")

        if result["image_path"]:
            print(f"   {result['image_path']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
