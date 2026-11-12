"""Find the facts that best match a question, or look up where an object was last seen.

    uv run python -m memory.search "where does he sleep"
    uv run python -m memory.search --object laptop

For a question, two searches run over the facts and their results are combined:
  by meaning   the question is embedded and compared with every fact's embedding
  by keyword   the question's words are matched against each fact's text
A fact that ranks well in both ends up on top.

The object lookup uses no embeddings. It reads the objects table, newest sighting first.
"""

import argparse
import sys

from memory import db, llm

# How many results each of the two searches contributes before they are combined.
CANDIDATES = 20

# Standard constant for reciprocal rank fusion. Larger values flatten the gap between ranks.
FUSION_K = 60

BY_MEANING = """
    select id from fact
     where embedding is not null
     order by embedding <=> %(vector)s::vector
     limit %(limit)s
"""

# plainto_tsquery joins the words with AND; swapping in OR lets a fact match on any of them.
BY_KEYWORD = """
    with words as (select replace(plainto_tsquery('english', %(text)s)::text, '&', '|')::tsquery as query)
    select id from fact, words
     where text_search @@ words.query
     order by ts_rank(text_search, words.query) desc
     limit %(limit)s
"""

DETAILS = """
    select fact.id, fact.kind, fact.session_id, fact.offset_seconds, fact.text, frame.image_path
      from fact left join frame on frame.id = fact.frame_id
     where fact.id = any(%s)
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
    """Return the facts that best match a question, best first."""
    vector = llm.embed(conn, [question], "embed-question")[0]

    by_meaning = [fact_id for (fact_id,) in conn.execute(BY_MEANING, {"vector": vector, "limit": CANDIDATES})]
    by_keyword = [fact_id for (fact_id,) in conn.execute(BY_KEYWORD, {"text": question, "limit": CANDIDATES})]

    ranked = fuse([by_meaning, by_keyword])[:limit]

    details = {
        fact_id: {"kind": kind, "session_id": session_id, "offset_seconds": float(offset), "text": text,
                  "image_path": image_path}
        for fact_id, kind, session_id, offset, text, image_path
        in conn.execute(DETAILS, ([fact_id for fact_id, _ in ranked],))
    }

    return [
        {
            "id": fact_id,
            "score": score,
            "matched": [name for name, hits in (("meaning", by_meaning), ("keyword", by_keyword)) if fact_id in hits],
            **details[fact_id],
        }
        for fact_id, score in ranked
    ]


def clock(seconds: float) -> str:
    """Format seconds as m:ss."""
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Search facts, or look up an object.")
    parser.add_argument("question", nargs="?")
    parser.add_argument("--object", help="show where an object with this name was last seen")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    if not args.question and not args.object:
        parser.error("give a question, or --object NAME")

    with db.connect() as conn:
        if args.object:
            sightings = db.last_seen(conn, args.object, args.limit)

            for name, relation, anchor, seen_at, setting, session_id, offset, image_path in sightings:
                print(f"{name}: {relation} {anchor}  [{setting}]  {seen_at.astimezone():%a %d %b %H:%M:%S}"
                      f"  session {session_id} at {clock(offset)}")
                print(f"   {image_path}")

            if not sightings:
                print(f"No object matching '{args.object}' has been seen.")

            return 0

        results = search(conn, args.question, args.limit)

    if not results:
        print("Nothing to search yet. Run the captions, transcript and facts steps first.")
        return 0

    for position, result in enumerate(results, start=1):
        matched = " + ".join(result["matched"])

        print(f"{position}. {result['kind']:<6} {clock(result['offset_seconds']):>5}  [{matched}]  {result['text']}")

        if result["image_path"]:
            print(f"   {result['image_path']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
