-- Rework of how frames are described and what gets embedded.
--   Objects are stored as object, relation, anchor ("water bottle", "on", "kitchen island").
--   The free-text room is replaced by a setting from a fixed list, plus coordinates and named places.
--   Search runs over small facts, each embedded with its context, instead of one paragraph per frame.

-- Captions written in the old shape can't be converted, so they are cleared and written again.
delete from frame_object;
update frame set summary = null, captioned_at = null;

alter table frame
    drop column text_search,
    drop column search_text,
    drop column embedding,
    drop column room,
    drop column text_seen,
    add column setting text;                 -- kind of space, from the fixed list in captions.py

alter table frame_object
    drop column location,
    add column relation text not null,       -- 'on', 'in', 'next to', ...
    add column anchor   text not null;       -- the thing it is on, in or next to; ideally something that stays put

create table frame_text (
    frame_id bigint not null references frame (id) on delete cascade,
    text     text not null,                  -- readable text, copied exactly
    surface  text not null                   -- what it is written on, e.g. 'whiteboard'
);

alter table audio_segment
    drop column text_search,
    drop column embedding;

-- Where a session happened. A recording has one location; the glasses will later give one per frame.
alter table session
    add column lat double precision,
    add column lon double precision;

create table place (
    id       bigint generated always as identity primary key,
    name     text not null unique,           -- e.g. 'Home'
    lat      double precision not null,
    lon      double precision not null,
    radius_m real not null default 75        -- a session within this distance counts as being at the place
);

create table fact (
    id             bigint generated always as identity primary key,
    session_id     text not null references session (id) on delete cascade,
    frame_id       bigint references frame (id) on delete cascade,   -- null for speech
    kind           text not null,            -- 'moment', 'object', 'text' or 'speech'
    at             timestamptz not null,
    offset_seconds real not null,            -- seconds from the start of the session
    text           text not null,            -- the fact with its context; this is what gets embedded
    embedding      vector(1536),
    text_search    tsvector generated always as (to_tsvector('english', text)) stored
);

create index fact_embedding on fact using hnsw (embedding vector_cosine_ops);
create index fact_text_search on fact using gin (text_search);
create index fact_session on fact (session_id, at);
