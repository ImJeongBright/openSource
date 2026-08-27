# ============================================
# OpenSQL AI 문서 검색 시스템 — Makefile
# ============================================

.PHONY: help install run-api run-worker run-mcp psql init-db test lint evaluate benchmark explain-search rebuild-index provision-roles

help: ## 사용 가능한 명령어 목록
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------
# 환경 구성
# ------------------------------------------------------------------

install: ## 의존 패키지 설치
	pip install -r requirements.txt

env: ## .env 파일이 없으면 .env.example 복사
	@[ -f .env ] || (cp .env.example .env && echo ".env 파일 생성 완료. 값을 직접 채워주세요.")

# ------------------------------------------------------------------
# 실행
# ------------------------------------------------------------------

run-api: ## FastAPI 서버 실행 (개발용)
	uvicorn src.api.routes:app --host 0.0.0.0 --port 8000 --reload

run-worker: ## 비동기 Worker 데몬 실행
	python -m src.worker.daemon

run-mcp: ## MCP 서버 실행
	python mcp/server.py

# ------------------------------------------------------------------
# DB 관리
# ------------------------------------------------------------------

psql: ## psql 접속 (개발자 A/B 공통)
	psql -h $${OPENSQL_HOST:-localhost} -p $${OPENSQL_PORT:-5432} \
	     -U $${OPENSQL_USER:-app_user} -d $${OPENSQL_DB:-doc_search}

init-db: ## 전체 DDL 및 시드 데이터 적용 (초기 1회)
	@echo "DB 초기화 시작..."
	bash sql/run_all.sh
	@echo "DB 초기화 완료!"

reset-db: ## DB 스키마 초기화 (주의: 모든 데이터 삭제)
	@read -p "정말 초기화하시겠습니까? (yes/no): " confirm; \
	[ "$$confirm" = "yes" ] && psql -h $${OPENSQL_HOST:-localhost} \
	    -U $${OPENSQL_USER:-app_user} -d $${OPENSQL_DB:-doc_search} \
	    -c "DROP SCHEMA IF EXISTS doc_search CASCADE;" \
	    && $(MAKE) init-db || echo "취소되었습니다."

# ------------------------------------------------------------------
# 테스트 및 품질
# ------------------------------------------------------------------

test: ## pytest 실행
	pytest tests/ -v

lint: ## ruff linter 실행
	ruff check src/ mcp/ tests/

evaluate: ## 검색 품질 Recall@K/MRR 평가 (DATASET=...)
	python scripts/evaluate_search.py $${DATASET:-tests/fixtures/search_quality.example.jsonl}

benchmark: ## 동시 검색 응답시간 측정 (DATASET=...)
	python scripts/benchmark_search.py $${DATASET:-tests/fixtures/search_quality.example.jsonl}

explain-search: ## 실제 검색 실행계획 확인 (QUERY=...)
	python scripts/explain_search.py "$${QUERY:-데이터베이스 장애 복구 절차}"

rebuild-index: ## HNSW 인덱스를 온라인 방식으로 재구축
	psql -h $${OPENSQL_HOST:-localhost} -p $${OPENSQL_PORT:-5432} \
	     -U $${OPENSQL_USER:-app_user} -d $${OPENSQL_DB:-doc_search} \
	     -f sql/maintenance/rebuild_hnsw.sql

provision-roles: ## API/Worker/MCP 최소 권한 DB 계정 생성·회전
	sudo ./scripts/provision_runtime_roles.sh
