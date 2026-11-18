alter table job add column task jsonb;

create unique index job_recording on job ((task ->> 'recording')) where task ->> 'recording' is not null;
