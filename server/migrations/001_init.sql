-- Sessions and their frames. Captions, transcripts and embeddings are added by later migrations.

create extension if not exists vector;

create table session (
    id               text primary key,      -- the start time, e.g. 2026-11-11T19-54-08
    source           text not null,         -- 'recording' or 'glasses'
    started_at       timestamptz not null,
    duration_seconds real not null,
    audio_path       text                   -- relative to the data folder; null when there is no sound
);

create table frame (
    id          bigint generated always as identity primary key,
    session_id  text not null references session (id) on delete cascade,
    captured_at timestamptz not null,
    image_path  text not null unique,       -- relative to the data folder
    fingerprint text not null               -- 64-bit fingerprint as 16 hex characters
);

create index frame_session_time on frame (session_id, captured_at);
