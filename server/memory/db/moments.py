"""Moments, their frames, captions and transcripts."""

import json
from pathlib import Path

import psycopg



def position(frame: dict) -> tuple:
    """Return a manifest frame's (x, y, z, yaw), or Nones if the recording had no positions."""
    pose = frame.get("position")

    return tuple(pose) if pose else (None, None, None, None)


def save_moment(conn: psycopg.Connection, moment_dir: Path) -> dict:
    """Store a moment folder's manifest, keeping the captions of frames that are unchanged."""
    manifest = json.loads((moment_dir / "manifest.json").read_text())
    moment_id = manifest["id"]
    folder = f"moments/{moment_id}"

    wanted = {(f"{folder}/{frame['file']}", frame["fingerprint"]): frame for frame in manifest["frames"]}

    with conn.transaction():
        conn.execute(
            "insert into moment (id, source, started_at, duration_seconds, audio_path)"
            " values (%(id)s, %(source)s, %(started_at)s, %(duration)s, %(audio)s)"
            " on conflict (id) do update set source = excluded.source, started_at = excluded.started_at,"
            " duration_seconds = excluded.duration_seconds, audio_path = excluded.audio_path",
            {
                "id": moment_id,
                "source": manifest["source"],
                "started_at": manifest["started_at"],
                "duration": manifest["duration_seconds"],
                "audio": f"{folder}/{manifest['audio']}" if manifest["audio"] else None,
            },
        )

        conn.execute("delete from location where moment_id = %s", (moment_id,))

        if manifest.get("lat") is not None and manifest.get("lon") is not None:
            conn.execute(
                "insert into location (moment_id, at, lat, lon) values (%s, %s, %s, %s)",
                (moment_id, manifest["started_at"], manifest["lat"], manifest["lon"]),
            )

        stored = {
            (image_path, fingerprint): (frame_id, captioned)
            for frame_id, image_path, fingerprint, captioned in conn.execute(
                "select id, image_path, fingerprint, captioned_at is not null from frame where moment_id = %s",
                (moment_id,),
            )
        }

        gone = [stored[key] for key in stored.keys() - wanted.keys()]
        conn.execute("delete from frame where id = any(%s)", ([frame_id for frame_id, _ in gone],))

        with conn.cursor() as cursor:
            cursor.executemany(
                "update frame set captured_at = %s, x = %s, y = %s, z = %s, yaw = %s where id = %s",
                [(wanted[key]["captured_at"], *position(wanted[key]), stored[key][0])
                 for key in wanted.keys() & stored.keys()],
            )

            cursor.executemany(
                "insert into frame (moment_id, captured_at, image_path, fingerprint, x, y, z, yaw)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                [(moment_id, wanted[key]["captured_at"], *key, *position(wanted[key]))
                 for key in wanted.keys() - stored.keys()],
            )

    return {
        "frames": len(wanted),
        "captions_kept": sum(stored[key][1] for key in wanted.keys() & stored.keys()),
        "captions_lost": sum(captioned for _, captioned in gone),
        "changed": bool(stored) and wanted.keys() != stored.keys(),
    }


FIRST_LOCATION = (
    " left join lateral (select lat, lon from location where location.moment_id = s.id order by at limit 1) l on true"
)


def list_moments(conn: psycopg.Connection) -> list[tuple]:
    """Return every moment, newest first, with where it started if known."""
    return conn.execute(
        "select s.id, s.source, s.started_at, s.duration_seconds, count(f.id), s.audio_path is not null,"
        "       l.lat, l.lon, s.title"
        f" from moment s left join frame f on f.moment_id = s.id{FIRST_LOCATION}"
        " group by s.id, l.lat, l.lon order by s.started_at desc"
    ).fetchall()


def moment_info(conn: psycopg.Connection, moment_id: str) -> tuple:
    """Return (started_at, lat, lon) for a moment, with where it started if known."""
    return conn.execute(
        f"select s.started_at, l.lat, l.lon from moment s{FIRST_LOCATION} where s.id = %s", (moment_id,)
    ).fetchone()


def frames_without_caption(conn: psycopg.Connection, moment_id: str, limit: int | None = None) -> list[tuple]:
    """Return (frame id, image path) for frames with no caption."""
    return conn.execute(
        "select id, image_path from frame where moment_id = %s and captioned_at is null"
        " order by captured_at limit %s",
        (moment_id, limit),
    ).fetchall()


def save_caption(conn: psycopg.Connection, frame_id: int, caption) -> None:
    """Store a frame's caption, replacing any earlier one."""
    conn.execute(
        "update frame set summary = %s, captioned_at = now() where id = %s",
        (caption.summary, frame_id),
    )

    conn.execute("delete from frame_object where frame_id = %s", (frame_id,))
    conn.execute("delete from frame_text where frame_id = %s", (frame_id,))

    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into frame_object (frame_id, name, location) values (%s, %s, %s)",
            [(frame_id, item.name, item.location) for item in caption.objects],
        )

        cursor.executemany(
            "insert into frame_text (frame_id, text, surface) values (%s, %s, %s)",
            [(frame_id, item.text, item.surface) for item in caption.texts],
        )


def clear_captions(conn: psycopg.Connection, moment_id: str) -> None:
    """Mark every frame of a moment as not captioned."""
    conn.execute("update frame set captioned_at = null where moment_id = %s", (moment_id,))


def captioned_frames(conn: psycopg.Connection, moment_id: str) -> list[dict]:
    """Return every captioned frame of a moment, oldest first."""
    rows = conn.execute(
        "select f.id, f.image_path, f.captured_at, extract(epoch from f.captured_at - s.started_at),"
        "       case when f.x is null then null else json_build_array(f.x, f.y, f.z, f.yaw) end,"
        "       f.summary, r.name,"
        "       (select coalesce(json_agg(json_build_array(o.name, o.location)), '[]')"
        "          from frame_object o where o.frame_id = f.id),"
        "       (select coalesce(json_agg(json_build_array(t.text, t.surface)), '[]')"
        "          from frame_text t where t.frame_id = f.id)"
        "       ,(select coalesce(json_agg(distinct jsonb_build_object('id', p.id, 'name', p.name, 'face_id',"
        "          (select f2.id from face f2 where f2.person_id = p.id order by f2.w * f2.h * f2.score desc limit 1))), '[]')"
        "          from face fc join person p on p.id = fc.person_id where fc.frame_id = f.id)"
        "  from frame f join moment s on s.id = f.moment_id left join room r on r.id = f.room_id"
        " where f.moment_id = %s and f.captioned_at is not null"
        " order by f.captured_at",
        (moment_id,),
    ).fetchall()

    return [
        {
            "id": frame_id,
            "image_path": image_path,
            "at": captured_at,
            "offset_seconds": float(offset),
            "summary": summary,
            "objects": [tuple(item) for item in objects],
            "texts": [tuple(item) for item in texts],
            "people": people,
            "position": pose,
            "room": room,
        }
        for frame_id, image_path, captured_at, offset, pose, summary, room, objects, texts, people in rows
    ]


def save_transcript(conn: psycopg.Connection, moment_id: str, segments) -> None:
    """Store a moment's transcript, replacing any earlier one."""
    conn.execute("delete from audio_segment where moment_id = %s", (moment_id,))

    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into audio_segment (moment_id, start_at, end_at, offset_seconds, speaker, text)"
            " select id, started_at + make_interval(secs => %s), started_at + make_interval(secs => %s), %s, %s, %s"
            " from moment where id = %s",
            [
                (segment.start, segment.end, segment.start, segment.speaker, segment.text.strip(), moment_id)
                for segment in segments
                if segment.text.strip()
            ],
        )


def transcript(conn: psycopg.Connection, moment_id: str) -> list[tuple]:
    """Return the transcript as (offset seconds, speaker, text)."""
    return conn.execute(
        "select offset_seconds, speaker, text from audio_segment where moment_id = %s order by start_at",
        (moment_id,),
    ).fetchall()


def transcript_lines(conn: psycopg.Connection, moment_id: str) -> list[dict]:
    """Return the transcript lines with start and end offsets."""
    rows = conn.execute(
        "select start_at, offset_seconds, offset_seconds + extract(epoch from end_at - start_at), speaker, text"
        " from audio_segment where moment_id = %s order by start_at",
        (moment_id,),
    ).fetchall()

    return [
        {"at": start_at, "start": float(start), "end": float(end), "speaker": speaker, "text": text}
        for start_at, start, end, speaker, text in rows
    ]


def covers(conn: psycopg.Connection) -> dict[str, str]:
    """Return the first frame's image path for each moment."""
    rows = conn.execute("select distinct on (moment_id) moment_id, image_path from frame order by moment_id, captured_at")

    return dict(rows.fetchall())


def counts(conn: psycopg.Connection) -> dict:
    """Return how many moments, frames and facts are stored."""
    moments, frames, facts = conn.execute(
        "select (select count(*) from moment), (select count(*) from frame), (select count(*) from fact)"
    ).fetchone()

    return {"moments": moments, "frames": frames, "facts": facts}


def latest_moment_id(conn: psycopg.Connection) -> str | None:
    """Return the id of the newest moment."""
    row = conn.execute("select id from moment order by started_at desc limit 1").fetchone()

    return row[0] if row else None


def moment_duration(conn: psycopg.Connection, moment_id: str) -> float:
    """Return how long a moment lasts, in seconds."""
    return float(conn.execute("select duration_seconds from moment where id = %s", (moment_id,)).fetchone()[0])


def moment_ids_oldest_first(conn: psycopg.Connection) -> list[str]:
    """Return every moment's id in the order they were recorded."""
    return [row[0] for row in conn.execute("select id from moment order by started_at")]
