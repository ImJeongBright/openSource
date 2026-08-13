-- ============================================================
-- sql/08_seed.sql
-- 초기 임베딩 모델 시드 데이터
-- 담당: 개발자 B
-- ============================================================

INSERT INTO doc_search.embedding_models (model_name, model_version, provider, dimensions, max_tokens)
VALUES 
    ('text-embedding-3-small', '2024-01-01', 'openai', 1536, 8191),
    ('text-embedding-3-large', '2024-01-01', 'openai', 3072, 8191)
ON CONFLICT (model_name, model_version) DO NOTHING;

COMMENT ON TABLE doc_search.embedding_models IS '사용된 임베딩 모델 레지스트리 (초기 시드 데이터 포함)';
