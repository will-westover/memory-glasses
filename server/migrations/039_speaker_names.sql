alter table audio_segment add column if not exists person_id bigint references person (id) on delete set null;
alter table audio_segment add column if not exists named_by_you boolean not null default false;
create index if not exists audio_segment_person on audio_segment (person_id) where person_id is not null;
