alter table entity add column if not exists merged_into bigint references entity (id) on delete set null;
alter table entity add column if not exists merge_reason text;
alter table entity add column if not exists embedding halfvec(1536);
create index if not exists entity_merged_into on entity (merged_into);
create index if not exists entity_embedding on entity using hnsw (embedding halfvec_cosine_ops);
