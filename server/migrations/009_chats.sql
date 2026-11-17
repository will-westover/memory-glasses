create table conversation (
    id         uuid primary key default gen_random_uuid(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table turn (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversation (id) on delete cascade,
    asked_at        timestamptz not null default now(),
    question        text not null,
    answer          text,
    found           boolean,
    problem         text,
    model_calls     integer,
    seconds         real,
    usd             numeric(12, 6)
);

create index turn_conversation on turn (conversation_id, asked_at);

create table turn_source (
    turn_id        uuid not null references turn (id) on delete cascade,
    position       integer not null,
    fact_id        bigint not null,
    kind           text not null,
    at             timestamptz not null,
    session_id     text not null,
    offset_seconds real not null,
    text           text not null,
    image_path     text,
    primary key (turn_id, position)
);
