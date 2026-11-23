alter table moment add column video_file text;
alter table moment add column frames_per_minute real;
alter table moment add column extras text[] not null default '{}';
