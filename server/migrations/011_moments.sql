alter table session rename to moment;

alter table frame rename column session_id to moment_id;
alter table audio_segment rename column session_id to moment_id;
alter table fact rename column session_id to moment_id;
alter table turn_source rename column session_id to moment_id;

alter index frame_session_time rename to frame_moment_time;
alter index audio_segment_session_time rename to audio_segment_moment_time;
alter index fact_session rename to fact_moment;

update frame set image_path = regexp_replace(image_path, '^sessions/', 'moments/');
update moment set audio_path = regexp_replace(audio_path, '^sessions/', 'moments/');
update turn_source set image_path = regexp_replace(image_path, '^sessions/', 'moments/');

update fact set kind = 'frame' where kind = 'moment';
update turn_source set kind = 'frame' where kind = 'moment';
