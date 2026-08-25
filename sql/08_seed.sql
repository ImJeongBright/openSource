INSERT INTO doc_search.embedding_models (
    model_name,
    model_version,
    provider,
    dimensions,
    max_tokens,
    is_active,
    notes
) VALUES (
    'qwen3-embedding',
    '0.6b',
    'ollama',
    1024,
    32768,
    TRUE,
    'Open-weight Qwen3 Embedding 0.6B served locally through Ollama'
)
ON CONFLICT (model_name, model_version) DO UPDATE
SET provider = EXCLUDED.provider,
    dimensions = EXCLUDED.dimensions,
    max_tokens = EXCLUDED.max_tokens,
    is_active = TRUE,
    notes = EXCLUDED.notes;

UPDATE doc_search.embedding_models
SET is_active = FALSE
WHERE provider <> 'ollama'
  AND is_active = TRUE;
