# Phase 7 구현 완료 보고서

**담당**: Developer A
**단계**: Atomic 버전 전환 및 문서 처리 파이프라인 통합
**완료일**: 2026-08-26
**작업 브랜치**: `feat/api`
**구현 커밋**: `f3a6a91 feat(pipeline): integrate phase 7 document processing`

## 1. 완료 요약

Phase 4의 추출·청킹 모듈, Phase 5의 업로드·임베딩 모듈, Phase 6의 Worker lifecycle을 하나의 문서 처리 파이프라인으로 연결했습니다.

```text
Worker claim
  → 파일 경로 확인
  → 텍스트 추출
  → 버전별 청킹
  → chunks 멱등 저장
  → 미처리 청크 임베딩 배치 생성
  → embeddings/chunks/progress 트랜잭션 저장
  → 완료성 재집계
  → 이전 ACTIVE 보관 + 새 버전 ACTIVE 전환
  → change_log COMPLETED
```

## 2. 구현 내용

### 2.1 파이프라인 Runner

[src/pipeline/runner.py](../../src/pipeline/runner.py)에 기본 Worker handler를 구현했습니다.

- `version_id`와 `document_id`로 작업 컨텍스트를 조회
- 사용자가 제공한 경로를 직접 사용하지 않고 `UPLOAD_DIR/<version_id>.<확장자>` 규칙으로 원본 파일 탐색
- PDF/TXT/Markdown 텍스트 추출
- `document_versions.chunk_size`와 `chunk_overlap`을 사용한 버전별 청킹
- CPU·파일 작업은 `asyncio.to_thread()`로 실행
- 검색 가능한 텍스트가 없는 문서는 실패 처리
- Phase 6 Worker의 기본 `process_job()`에서 이 Runner를 호출

### 2.2 청크 멱등 저장

청크 저장은 하나의 DB 트랜잭션으로 처리합니다.

```sql
ON CONFLICT (version_id, chunk_index) DO NOTHING
```

재실행 시 기존 청크를 중복 삽입하지 않고, 저장된 실제 청크 수가 이번 추출 결과와 일치하는지 검증한 뒤 `total_chunks`를 갱신합니다. 개수가 다르면 트랜잭션을 rollback합니다.

### 2.3 임베딩 배치 및 재실행

미처리 청크 조회 조건은 `is_embedded` 값만 보지 않고 `embeddings.chunk_id` 존재 여부도 함께 확인합니다.

```sql
AND (NOT c.is_embedded OR e.chunk_id IS NULL)
```

각 배치는 다음 순서로 처리됩니다.

1. 설정된 배치 크기만큼 미처리 청크를 조회
2. 임베딩 API 호출
3. `save_embedding_batch()`로 벡터 저장
4. `embeddings` 삽입, `chunks.is_embedded` 갱신, `embedded_chunks` 재집계를 하나의 트랜잭션으로 처리

기존 벡터가 이미 존재하는 경우 `ON CONFLICT (chunk_id) DO NOTHING`으로 중복 저장을 피하고, 상태 불일치가 있으면 청크 상태를 복구합니다.

### 2.4 완료성 검증 및 Atomic 활성화

[src/pipeline/versioner.py](../../src/pipeline/versioner.py)의 `activate_version()`은 활성화 전에 다음 값을 DB에서 다시 집계합니다.

- 전체 청크 수
- 해당 버전의 임베딩 수
- 임베딩이 없는 청크 수
- `is_embedded = FALSE`인 청크 수

다음 조건을 모두 만족할 때만 활성화합니다.

```text
total_chunks > 0
total_chunks == embedded_chunks
missing_embeddings == 0
unmarked_chunks == 0
```

검증과 함수 호출은 같은 트랜잭션에서 수행하고 버전 행을 잠급니다. DB의 `doc_search.activate_version()` 함수는 문서 행을 잠근 뒤 기존 `ACTIVE` 버전을 `ARCHIVED`로 바꾸고 새 버전을 `ACTIVE`로 전환합니다.

### 2.5 Activation 멱등성

Worker가 활성화 직후 lease를 잃어 같은 작업을 재실행하는 경우를 처리했습니다.

- 이미 `ACTIVE`인 버전은 `activate_version()`이 성공 no-op으로 처리
- 이미 `ACTIVE` 또는 `ARCHIVED`인 버전의 stale job은 다시 `PROCESSING`으로 낮추지 않고 `COMPLETED`로 정리
- 기존 설치 DB에는 [V003__idempotent_activate_version.sql](../../sql/migrations/V003__idempotent_activate_version.sql) 적용
- clean install은 [sql/05_functions.sql](../../sql/05_functions.sql)에 반영된 함수 정의 사용

## 3. 테스트 및 검증

### 3.1 추가 테스트

- `tests/pipeline/test_runner.py`: 청크 트랜잭션, count mismatch rollback, 임베딩 배치, 전체 파이프라인 순서, ACTIVE 재실행
- `tests/pipeline/test_versioner.py`: 완료성 검증, 미완료 거부, ACTIVE 멱등성, 버전 없음 처리
- `tests/worker/test_daemon.py`: 활성화 후 lease 손실 시 stale job 정리
- `tests/sql/test_qwen_schema.py`: idempotent activation 함수와 V003 마이그레이션 계약

### 3.2 실행 결과

```text
106 passed, 1 skipped in 2.90s
All checks passed!
```

실행 명령:

```bash
TIKTOKEN_CACHE_DIR=/tmp/opensql-tiktoken-cache .venv/bin/pytest -q
.venv/bin/ruff check .
git diff --check
```

`skipped` 1건은 Ollama 실서비스 통합 테스트입니다. 현재 실행 환경에서는 Ollama가 `127.0.0.1:11434`에서 실행되지 않아 실제 임베딩 호출과 DB 통합 실행은 수행하지 못했습니다.

## 4. 데이터베이스 적용 순서

기존 설치 환경에는 다음 마이그레이션을 적용해야 합니다.

```bash
psql -v ON_ERROR_STOP=1 -f sql/migrations/V003__idempotent_activate_version.sql
```

새로 설치하는 환경은 `sql/run_all.sh`가 실행하는 `sql/05_functions.sql`에 idempotent 함수 정의가 포함되어 있습니다.

## 5. 남은 운영 검증

코드와 단위 테스트 범위의 Phase 7 구현은 완료했습니다. 다음은 실제 인프라가 준비된 후 수행할 운영 검증입니다.

1. OpenSQL에서 V003 마이그레이션 실행
2. Ollama의 `qwen3-embedding:0.6b` 모델과 1024차원 응답 확인
3. 실제 문서 업로드 후 Worker 처리 및 `ACTIVE` 전환 확인
4. 같은 문서의 두 버전 동시 처리에서 ACTIVE 버전이 하나만 남는지 확인
5. 임베딩 중 Worker 강제 종료 후 재시작하여 중복 벡터가 생기지 않는지 확인

Phase 8에서는 이 파이프라인이 활성화한 데이터에 대해 벡터 검색과 MCP 시나리오를 검증하면 됩니다.
