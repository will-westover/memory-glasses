alter table entity add column embedding vector(1536);
alter table entity add column merge_reason text;

create index entity_merged_into on entity (merged_into);
