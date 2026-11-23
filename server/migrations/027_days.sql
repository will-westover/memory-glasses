alter table episode alter column moment_id drop not null;
alter table episode add column day date;

create unique index episode_day on episode (day) where level = 'day';
