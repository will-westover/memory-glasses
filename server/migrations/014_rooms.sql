create table room (
    id         bigint generated always as identity primary key,
    name       text,
    lat        double precision,
    lon        double precision,
    map_path   text not null,
    updated_at timestamptz not null default now()
);

alter table moment add column room_id bigint references room (id) on delete set null;
