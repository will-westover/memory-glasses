update place set map_path = regexp_replace(map_path, '^spaces/', 'places/') where map_path like 'spaces/%';
