create table upload (
    id                uuid primary key default gen_random_uuid(),
    received_at       timestamptz not null default now(),
    frames_per_minute real not null,
    status            text not null default 'processing',
    step              text,
    moment_id         text,
    error             text
);
