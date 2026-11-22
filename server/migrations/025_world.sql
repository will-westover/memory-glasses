create table entity (
    id bigserial primary key,
    kind text not null,
    name text not null,
    aliases text[] not null default '{}',
    details text,
    person_id bigint references person (id) on delete cascade,
    crop_frame_id bigint references frame (id) on delete set null,
    crop_box real[],
    first_seen timestamptz,
    last_seen timestamptz,
    merged_into bigint references entity (id) on delete set null,
    confirmed boolean not null default false,
    created_at timestamptz not null default now()
);

create unique index entity_person on entity (person_id) where person_id is not null;

create table episode (
    id bigserial primary key,
    moment_id text not null references moment (id) on delete cascade,
    level text not null,
    parent_id bigint references episode (id) on delete cascade,
    start_at timestamptz not null,
    end_at timestamptz not null,
    title text,
    summary text
);

create index episode_moment on episode (moment_id, start_at);

create table sighting (
    id bigserial primary key,
    entity_id bigint not null references entity (id) on delete cascade,
    frame_id bigint not null references frame (id) on delete cascade,
    episode_id bigint references episode (id) on delete cascade,
    at timestamptz not null,
    place text,
    details text,
    box real[],
    source text not null default 'seen'
);

create index sighting_entity on sighting (entity_id, at);
create index sighting_frame on sighting (frame_id);

alter table fact add column entity_id bigint references entity (id) on delete set null;
alter table fact add column episode_id bigint references episode (id) on delete set null;
