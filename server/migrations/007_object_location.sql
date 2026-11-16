delete from frame_object;
update frame set summary = null, captioned_at = null;

alter table frame_object
    drop column relation,
    drop column anchor,
    add column location text not null;
