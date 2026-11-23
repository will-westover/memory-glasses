drop index if exists fact_embedding;
drop index if exists entity_embedding;
alter table fact alter column embedding type halfvec(1536) using embedding::halfvec(1536);
alter table entity alter column embedding type halfvec(1536) using embedding::halfvec(1536);
create index fact_embedding on fact using hnsw (embedding halfvec_cosine_ops);
create index entity_embedding on entity using hnsw (embedding halfvec_cosine_ops);
