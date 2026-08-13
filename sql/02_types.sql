-- ============================================================
-- sql/02_types.sql
-- ENUM 타입 정의
-- 담당: 개발자 A
-- ============================================================

-- 버전 처리 상태
CREATE TYPE doc_search.version_status AS ENUM (
    'PENDING',      -- 업로드 완료, 처리 대기 중
    'PROCESSING',   -- 파이프라인 처리 진행 중
    'ACTIVE',       -- 현재 검색 대상 활성 버전
    'ARCHIVED',     -- 이전 버전 (보존)
    'FAILED'        -- 처리 실패
);

-- 변경 이벤트 유형
CREATE TYPE doc_search.change_event_type AS ENUM (
    'UPLOAD',           -- 새 문서 업로드
    'UPDATE',           -- 기존 문서 새 버전 등록
    'DELETE',           -- 문서 삭제
    'EMBED_START',      -- 임베딩 처리 시작
    'EMBED_COMPLETE',   -- 임베딩 처리 완료
    'EMBED_FAIL',       -- 임베딩 처리 실패
    'VERSION_SWITCH'    -- ACTIVE 버전 전환
);

-- Worker 작업 상태
CREATE TYPE doc_search.job_status AS ENUM (
    'PENDING',      -- 처리 대기
    'PROCESSING',   -- 처리 중 (Worker가 락 보유)
    'COMPLETED',    -- 처리 완료
    'FAILED',       -- 처리 실패
    'DEAD_LETTER'   -- 최대 재시도 초과
);
