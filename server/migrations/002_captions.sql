-- What a model saw in each frame, and what every model call cost.

alter table frame
    add column summary      text,
    add column room         text,
    add column text_seen    text,
    add column captioned_at timestamptz;

create table frame_object (
    frame_id bigint not null references frame (id) on delete cascade,
    name     text not null,     -- e.g. 'laptop'
    location text not null      -- where it is in the scene, e.g. 'on the desk by the window'
);

create index frame_object_name on frame_object (lower(name));

create table api_cost (
    id            bigint generated always as identity primary key,
    created_at    timestamptz not null default now(),
    step          text not null,          -- which part of the pipeline made the call, e.g. 'caption'
    model         text not null,
    frame_id      bigint references frame (id) on delete set null,
    input_tokens  integer not null,
    cached_tokens integer not null,       -- the part of input_tokens billed at the cached rate
    output_tokens integer not null,
    usd           numeric(12, 6) not null
);
