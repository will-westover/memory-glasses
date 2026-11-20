create table recording (
    name text primary key,
    base text not null,
    part int not null default 1,
    started_at timestamptz not null,
    last_frame_at timestamptz not null default now(),
    photos int not null default 0,
    ended_at timestamptz,
    job_id uuid references job (id) on delete set null
);

create index recording_base on recording (base, part);
