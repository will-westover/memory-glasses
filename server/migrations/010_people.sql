create table person (
    id         bigint generated always as identity primary key,
    name       text,
    created_at timestamptz not null default now()
);

create table face (
    id        bigint generated always as identity primary key,
    frame_id  bigint not null references frame (id) on delete cascade,
    person_id bigint not null references person (id) on delete cascade,
    x         real not null,
    y         real not null,
    w         real not null,
    h         real not null,
    score     real not null,
    embedding vector(128) not null
);

create index face_frame on face (frame_id);
create index face_person on face (person_id);
