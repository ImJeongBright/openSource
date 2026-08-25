-- ============================================================
-- sql/05_functions.sql
-- 저장 함수 2종: activate_version, search_documents
-- 담당: 개발자 A
-- ============================================================

-- ------------------------------------------------------------
-- activate_version(): ACTIVE 버전 원자적 전환
-- 단일 트랜잭션 내에서 기존 ACTIVE → ARCHIVED, 신규 → ACTIVE
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION doc_search.activate_version(
    p_version_id UUID
) RETURNS VOID AS $$
DECLARE
    v_document_id UUID;
BEGIN
    -- 대상 버전의 document_id 조회 (PROCESSING 상태여야만 전환 허용)
    SELECT document_id INTO v_document_id
    FROM doc_search.document_versions
    WHERE id = p_version_id AND status = 'PROCESSING';

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Version % not found or not in PROCESSING status', p_version_id;
    END IF;

    -- 같은 문서의 동시 버전 전환을 직렬화한다.
    PERFORM 1
    FROM doc_search.documents
    WHERE id = v_document_id
    FOR UPDATE;

    -- 기존 ACTIVE 버전 → ARCHIVED
    UPDATE doc_search.document_versions
    SET status     = 'ARCHIVED',
        updated_at = NOW()
    WHERE document_id = v_document_id
      AND status = 'ACTIVE';

    -- 신규 버전 → ACTIVE
    UPDATE doc_search.document_versions
    SET status                  = 'ACTIVE',
        updated_at              = NOW(),
        activated_at            = NOW(),
        processing_completed_at = NOW()
    WHERE id = p_version_id;

    -- 버전 전환 이벤트 로그
    INSERT INTO doc_search.change_log (event_type, status, document_id, version_id)
    VALUES ('VERSION_SWITCH', 'COMPLETED', v_document_id, p_version_id);
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION doc_search.activate_version(UUID) IS
    '신규 버전을 ACTIVE로 원자적으로 전환한다. 기존 ACTIVE 버전은 ARCHIVED로 변경된다.';

-- ------------------------------------------------------------
-- search_documents(): pgvector 코사인 유사도 검색
-- 검색 시 hnsw.ef_search 파라미터 조정으로 품질/속도 트레이드오프 가능
-- 기본값: SET hnsw.ef_search = 40;
-- ------------------------------------------------------------
CREATE OR REPLACE FUNCTION doc_search.search_documents(
    p_query_vector  vector(1536),
    p_top_k         INTEGER        DEFAULT 5,
    p_category      VARCHAR        DEFAULT NULL,
    p_min_score     FLOAT          DEFAULT 0.0
) RETURNS TABLE (
    chunk_id        UUID,
    chunk_text      TEXT,
    document_id     UUID,
    document_title  VARCHAR,
    version_number  INTEGER,
    page_number     INTEGER,
    section_title   VARCHAR,
    similarity      FLOAT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        adc.chunk_id,
        adc.chunk_text,
        adc.document_id,
        adc.document_title,
        adc.version_number,
        adc.page_number,
        adc.section_title,
        (1 - (adc.vector <=> p_query_vector))::FLOAT AS similarity
    FROM doc_search.active_document_chunks adc
    WHERE
        (p_category IS NULL OR adc.category = p_category)
        AND (1 - (adc.vector <=> p_query_vector)) >= p_min_score
    ORDER BY adc.vector <=> p_query_vector  -- 코사인 거리 오름차순 = 유사도 내림차순
    LIMIT p_top_k;
END;
$$ LANGUAGE plpgsql STABLE;

COMMENT ON FUNCTION doc_search.search_documents(vector, INTEGER, VARCHAR, FLOAT) IS
    'HNSW 인덱스를 이용한 코사인 유사도 기반 ANN 검색 함수. active_document_chunks 뷰 기반.';
