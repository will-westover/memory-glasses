alter table fact add column end_offset_seconds real;

create table fact_frame (
    fact_id bigint not null references fact (id) on delete cascade,
    frame_id bigint not null references frame (id) on delete cascade,
    primary key (fact_id, frame_id)
);

create index fact_frame_frame on fact_frame (frame_id);
