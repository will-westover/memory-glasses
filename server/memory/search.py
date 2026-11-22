"""Find the facts that best match a question, by meaning and by keyword."""

import argparse
import sys
from bisect import bisect_right
from datetime import datetime

from memory import db, llm
from memory.config import minutes_seconds

CANDIDATES_PER_RANKING = 20

FUSION_K = 60

PER_STRETCH_AND_KIND = 2

IN_RANGE_SQL = """
    (%(start)s::timestamptz is null
     or at + make_interval(secs => coalesce(end_offset_seconds, offset_seconds) - offset_seconds) >= %(start)s::timestamptz)
    and (%(end)s::timestamptz is null or at <= %(end)s::timestamptz)
    and (%(moments)s::text[] is null or moment_id = any(%(moments)s::text[]))
"""

BY_MEANING_SQL = f"""
    select id from fact
     where embedding is not null and {IN_RANGE_SQL}
     order by embedding <=> %(vector)s::vector
     limit %(limit)s
"""

BY_KEYWORD_SQL = f"""
    with words as (select replace(plainto_tsquery('english', %(text)s)::text, '&', '|')::tsquery as query)
    select id from fact, words
     where text_search @@ words.query and {IN_RANGE_SQL}
     order by ts_rank(text_search, words.query) desc
     limit %(limit)s
"""


def fuse(rankings: list[list], k: int = FUSION_K) -> list[tuple]:
    """Combine ranked lists into one by reciprocal rank fusion, best first, as (item, score)."""
    scores: dict = {}

    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)

    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def stretch_starts(conn, moment_ids: list[str]) -> dict[str, list[float]]:
    """Return where each moment's stretches start, from its summaries, in order."""
    starts: dict[str, list[float]] = {moment_id: [] for moment_id in moment_ids}
    rows = conn.execute(
        "select moment_id, offset_seconds from fact where kind = 'summary' and moment_id = any(%s)"
        " order by offset_seconds",
        (moment_ids,),
    )

    for moment_id, offset in rows:
        starts[moment_id].append(float(offset))

    return starts


def spread(
    facts: list[dict], starts: dict[str, list[float]], limit: int, per_group: int = PER_STRETCH_AND_KIND
) -> list[dict]:
    """Keep the facts in order, but at most `per_group` of each kind from any one stretch, up to `limit`."""
    kept, counts = [], {}

    for fact in facts:
        stretch = bisect_right(starts.get(fact["moment_id"], []), fact["offset_seconds"] + 0.01)
        group = (fact["moment_id"], stretch, fact["kind"])

        if counts.get(group, 0) < per_group:
            counts[group] = counts.get(group, 0) + 1
            kept.append(fact)

        if len(kept) == limit:
            break

    return kept


def search(
    conn,
    question: str,
    limit: int = 5,
    start: datetime | None = None,
    end: datetime | None = None,
    moments: list[str] | None = None,
) -> list[dict]:
    """Return the facts that best match a question, best first, from only the given moments when some are named."""
    vector = llm.embed(conn, [question], "embed-question")[0]
    query_params = {
        "vector": vector,
        "text": question,
        "limit": CANDIDATES_PER_RANKING,
        "start": start,
        "end": end,
        "moments": moments,
    }

    if start or end or moments:
        conn.execute("set local hnsw.iterative_scan = relaxed_order")

    by_meaning = [fact_id for (fact_id,) in conn.execute(BY_MEANING_SQL, query_params)]
    by_keyword = [fact_id for (fact_id,) in conn.execute(BY_KEYWORD_SQL, query_params)]

    ranked = fuse([by_meaning, by_keyword])
    fact_by_id = {fact["id"]: fact for fact in db.facts_by_id(conn, [fact_id for fact_id, _ in ranked])}
    found = [
        {
            **fact_by_id[fact_id],
            "score": score,
            "matched": [name for name, hits in (("meaning", by_meaning), ("keyword", by_keyword)) if fact_id in hits],
        }
        for fact_id, score in ranked
        if fact_id in fact_by_id
    ]

    return spread(found, stretch_starts(conn, list({fact["moment_id"] for fact in found})), limit)


def search_within(conn, question: str, limit: int, window: tuple, moments: list[str] | None = None) -> list[dict]:
    """Search inside the question's time window first, and everywhere only if nothing there matches."""
    start, end = window

    if start and end:
        found = search(conn, question, limit, start, end, moments)

        if found:
            return found

    return search(conn, question, limit, moments=moments)


def print_results(results: list[dict]) -> None:
    for position, result in enumerate(results, start=1):
        matched = " + ".join(result["matched"])

        print(
            f"{position}. {result['kind']:<6} {minutes_seconds(result['offset_seconds']):>5}  [{matched}]  {result['text']}"
        )

        if result["image_path"]:
            print(f"   {result['image_path']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Search the facts.")
    parser.add_argument("question")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    with db.connect() as conn:
        results = search(conn, args.question, args.limit)

    if not results:
        print("Nothing to search yet. Run the captions, transcript and facts steps first.")
        return 0

    print_results(results)

    return 0


if __name__ == "__main__":
    sys.exit(main())
