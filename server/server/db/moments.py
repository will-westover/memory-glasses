"""Moments, their frames, captions and transcripts."""

import json
from pathlib import Path

import psycopg


def frame_position(frame: dict) -> tuple:
    """Return a manifest frame's (x, y, z, yaw), or Nones if the recording had no positions."""
    pose = frame.get("position")

    return tuple(pose) if pose else (None, None, None, None)


def moment_files(manifest: dict) -> dict:
    """Return what a manifest says was kept with a moment: its video, frame rate and extra files."""
    return {
        "video": manifest.get("video_file"),
        "rate": manifest.get("frames_per_minute"),
        "extras": manifest.get("extras", []),
    }


def save_moment_files(conn: psycopg.Connection, moment_id: str, manifest: dict) -> None:
    """Store what a manifest says was kept with a moment."""
    conn.execute(
        "update moment set video_file = %(video)s, frames_per_minute = %(rate)s, extras = %(extras)s where id = %(id)s",
        {"id": moment_id, **moment_files(manifest)},
    )


def kept_files(conn: psycopg.Connection, moment_id: str) -> dict:
    """Return whether a moment kept its video and a lidar mesh, and its frame rate."""
    video, rate, extras = conn.execute(
        "select video_file, frames_per_minute, extras from moment where id = %s", (moment_id,)
    ).fetchone()

    return {"has_video": video is not None, "frames_per_minute": rate, "has_mesh": "mesh.obj" in extras}


def save_moment(conn: psycopg.Connection, moment_dir: Path) -> dict:
    """Store a moment folder's manifest, keeping the captions of frames that are unchanged."""
    manifest = json.loads((moment_dir / "manifest.json").read_text())
    moment_id = manifest["id"]
    folder = f"moments/{moment_id}"

    manifest_frames = {(f"{folder}/{frame['file']}", frame["fingerprint"]): frame for frame in manifest["frames"]}

    with conn.transaction():
        conn.execute(
            "insert into moment (id, source, started_at, duration_seconds, audio_path, video_file, frames_per_minute, extras)"
            " values (%(id)s, %(source)s, %(started_at)s, %(duration)s, %(audio)s, %(video)s, %(rate)s, %(extras)s)"
            " on conflict (id) do update set source = excluded.source, started_at = excluded.started_at,"
            " duration_seconds = excluded.duration_seconds, audio_path = excluded.audio_path,"
            " video_file = excluded.video_file, frames_per_minute = excluded.frames_per_minute, extras = excluded.extras",
            {
                "id": moment_id,
                "source": manifest["source"],
                "started_at": manifest["started_at"],
                "duration": manifest["duration_seconds"],
                "audio": f"{folder}/{manifest['audio']}" if manifest["audio"] else None,
                **moment_files(manifest),
            },
        )

        conn.execute("delete from location where moment_id = %s", (moment_id,))

        if manifest.get("lat") is not None and manifest.get("lon") is not None:
            conn.execute(
                "insert into location (moment_id, at, lat, lon) values (%s, %s, %s, %s)",
                (moment_id, manifest["started_at"], manifest["lat"], manifest["lon"]),
            )

        stored_frames = {
            (image_path, fingerprint): (frame_id, captioned)
            for frame_id, image_path, fingerprint, captioned in conn.execute(
                "select id, image_path, fingerprint, captioned_at is not null from frame where moment_id = %s",
                (moment_id,),
            )
        }

        removed_frames = [stored_frames[key] for key in stored_frames.keys() - manifest_frames.keys()]
        conn.execute("delete from frame where id = any(%s)", ([frame_id for frame_id, _ in removed_frames],))

        with conn.cursor() as cursor:
            cursor.executemany(
                "update frame set captured_at = %s, x = %s, y = %s, z = %s, yaw = %s where id = %s",
                [
                    (manifest_frames[key]["captured_at"], *frame_position(manifest_frames[key]), stored_frames[key][0])
                    for key in manifest_frames.keys() & stored_frames.keys()
                ],
            )

            cursor.executemany(
                "insert into frame (moment_id, captured_at, image_path, fingerprint, x, y, z, yaw)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (moment_id, manifest_frames[key]["captured_at"], *key, *frame_position(manifest_frames[key]))
                    for key in manifest_frames.keys() - stored_frames.keys()
                ],
            )

    return {
        "frames": len(manifest_frames),
        "captions_kept": sum(stored_frames[key][1] for key in manifest_frames.keys() & stored_frames.keys()),
        "captions_lost": sum(captioned for _, captioned in removed_frames),
        "changed": bool(stored_frames) and manifest_frames.keys() != stored_frames.keys(),
    }


FIRST_LOCATION_JOIN = (
    " left join lateral (select lat, lon from location where location.moment_id = s.id order by at limit 1) l on true"
)


def list_moments(conn: psycopg.Connection) -> list[tuple]:
    """Return every moment, newest first, with where it started if known."""
    return conn.execute(
        "select s.id, s.source, s.started_at, s.duration_seconds, count(f.id), s.audio_path is not null,"
        "       l.lat, l.lon, s.title"
        f" from moment s left join frame f on f.moment_id = s.id{FIRST_LOCATION_JOIN}"
        " group by s.id, l.lat, l.lon order by s.started_at desc"
    ).fetchall()


def recording_list(conn: psycopg.Connection, moments: list[str] | None = None) -> list[dict]:
    """Return every recording, or only the named ones, newest first, with title, start, length and where it started."""
    rows = conn.execute(
        f"select s.id, s.title, s.started_at, s.duration_seconds, l.lat, l.lon from moment s{FIRST_LOCATION_JOIN}"
        " where %(moments)s::text[] is null or s.id = any(%(moments)s::text[]) order by s.started_at desc",
        {"moments": moments},
    )

    return [
        {"id": moment_id, "title": title, "start": start, "seconds": seconds, "lat": lat, "lon": lon}
        for moment_id, title, start, seconds, lat, lon in rows
    ]


def moment_start_and_location(conn: psycopg.Connection, moment_id: str) -> tuple:
    """Return (started_at, lat, lon) for a moment, with where it started if known."""
    return conn.execute(
        f"select s.started_at, l.lat, l.lon from moment s{FIRST_LOCATION_JOIN} where s.id = %s", (moment_id,)
    ).fetchone()


def frames_without_caption(conn: psycopg.Connection, moment_id: str, limit: int | None = None) -> list[tuple]:
    """Return (frame id, image path, seconds into the moment) for frames with no caption."""
    return conn.execute(
        "select f.id, f.image_path, extract(epoch from f.captured_at - m.started_at) from frame f"
        " join moment m on m.id = f.moment_id where f.moment_id = %s and f.captioned_at is null"
        " order by f.captured_at limit %s",
        (moment_id, limit),
    ).fetchall()


def clean_box(box: list[float]) -> list[float] | None:
    """Return a box of four fractions kept inside the picture, or None if it isn't one."""
    if len(box) != 4:
        return None

    left, top, width, height = (min(max(value, 0.0), 1.0) for value in box)

    return [left, top, min(width, 1 - left), min(height, 1 - top)]


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
            "insert into frame_object (frame_id, name, location, details, box) values (%s, %s, %s, %s, %s)",
            [(frame_id, item.name, item.location, item.details, clean_box(item.box)) for item in caption.objects],
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
        "       (select coalesce(json_agg(json_build_object('name', o.name, 'location', o.location,"
        "          'details', o.details, 'box', o.box)), '[]')"
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
            "objects": objects,
            "texts": [tuple(item) for item in texts],
            "people": people,
            "position": pose,
            "room": room,
        }
        for frame_id, image_path, captured_at, offset, pose, summary, room, objects, texts, people in rows
    ]


def save_transcript(conn: psycopg.Connection, moment_id: str, lines) -> None:
    """Store a moment's transcript lines, each with its voiceprint, replacing any earlier ones."""
    conn.execute("delete from audio_segment where moment_id = %s", (moment_id,))

    with conn.cursor() as cursor:
        cursor.executemany(
            "insert into audio_segment (moment_id, start_at, end_at, offset_seconds, speaker, text, voice)"
            " select id, started_at + make_interval(secs => %s), started_at + make_interval(secs => %s), %s, %s, %s, %s"
            " from moment where id = %s",
            [
                (
                    line.start,
                    line.end,
                    line.start,
                    line.speaker,
                    line.text.strip(),
                    "[" + ",".join(f"{value:.6f}" for value in line.voice) + "]" if line.voice is not None else None,
                    moment_id,
                )
                for line in lines
                if line.text.strip()
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
    rows = conn.execute(
        "select distinct on (moment_id) moment_id, image_path from frame order by moment_id, captured_at"
    )

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


def moment_ids_oldest_first(conn: psycopg.Connection) -> list[str]:
    """Return every moment's id in the order they were recorded."""
    return [row[0] for row in conn.execute("select id from moment order by started_at")]


def delete_moment(conn: psycopg.Connection, moment_id: str) -> list[str]:
    """Delete a moment, its jobs and glasses recordings, and unnamed people left with no faces; return the recordings."""
    recordings = [
        name
        for (name,) in conn.execute(
            "select r.name from recording r join job j on j.id = r.job_id where j.moment_id = %s", (moment_id,)
        )
    ]

    conn.execute("delete from recording where name = any(%s)", (recordings,))
    conn.execute("delete from job where moment_id = %s", (moment_id,))
    conn.execute("delete from moment where id = %s", (moment_id,))
    conn.execute(
        "delete from person p where p.name is null and not exists (select 1 from face f where f.person_id = p.id)"
    )

    return recordings
