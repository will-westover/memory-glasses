-- Search over frames and speech, two ways: by meaning (embedding) and by exact words (text_search).

alter table frame
    add column search_text text,           -- the caption, objects and text seen, joined: what gets embedded
    add column embedding   vector(1536),
    add column text_search tsvector generated always as (to_tsvector('english', coalesce(search_text, ''))) stored;

alter table audio_segment
    add column embedding   vector(1536),
    add column text_search tsvector generated always as (to_tsvector('english', text)) stored;

create index frame_embedding on frame using hnsw (embedding vector_cosine_ops);
create index frame_text_search on frame using gin (text_search);

create index audio_segment_embedding on audio_segment using hnsw (embedding vector_cosine_ops);
create index audio_segment_text_search on audio_segment using gin (text_search);
