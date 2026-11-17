alter table room rename to space;
alter table space alter column map_path drop not null;
alter table space add column named boolean not null default false;

alter table moment rename column room_id to space_id;
alter table moment add column in_space_map boolean not null default false;
update moment set in_space_map = true where space_id is not null;

create table room (
    id          bigint generated always as identity primary key,
    space_id    bigint not null references space (id) on delete cascade,
    name        text not null,
    description text,
    named       boolean not null default false
);

create index room_space on room (space_id);

alter table frame add column room_id bigint references room (id) on delete set null;
