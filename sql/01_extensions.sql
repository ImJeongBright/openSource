-- ============================================================
-- sql/01_extensions.sql
-- Extension 활성화 및 Schema 초기화
-- 담당: 개발자 A
-- ============================================================

-- pgvector: 벡터 유사도 검색 지원
CREATE EXTENSION IF NOT EXISTS vector;

-- UUID는 PostgreSQL 17 내장 gen_random_uuid() 함수를 사용한다.

-- 전용 스키마 생성
CREATE SCHEMA IF NOT EXISTS doc_search;

-- 이 세션에서 기본 search_path 설정 (선택적)
-- 애플리케이션 코드에서는 스키마명을 명시적으로 사용하도록 권장
SET search_path TO doc_search, public;
