-- What was said in each session, as timed lines.

create table audio_segment (
    id             bigint generated always as identity primary key,
    session_id     text not null references session (id) on delete cascade,
    start_at       timestamptz not null,
    end_at         timestamptz not null,
    offset_seconds real not null,          -- seconds from the start of the session's audio
    speaker        text,                   -- the model's label for who spoke, e.g. 'A'; not a real name
    text           text not null
);

create index audio_segment_session_time on audio_segment (session_id, start_at);
