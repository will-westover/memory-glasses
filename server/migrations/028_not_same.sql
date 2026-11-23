create table not_same (
    frame_id bigint not null references frame (id) on delete cascade,
    other_frame_id bigint not null references frame (id) on delete cascade,
    primary key (frame_id, other_frame_id)
);
