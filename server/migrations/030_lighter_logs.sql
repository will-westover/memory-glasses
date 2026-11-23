update model_call set request = null, response = null where step in ('agent', 'rag');
