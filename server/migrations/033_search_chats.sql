create extension if not exists pg_trgm;
create index if not exists message_text_trigram on message using gin ((content::text) gin_trgm_ops);
create index if not exists conversation_title_trigram on conversation using gin (title gin_trgm_ops);
