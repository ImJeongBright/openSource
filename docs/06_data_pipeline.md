# 데이터 파이프라인 설계 (Data Pipeline Design)

**프로젝트명**: OpenSQL 기반 AI 문서 검색 및 버전 관리 시스템  
**문서 버전**: v1.0  
**작성일**: 2026-08-05

---

## 1. 파이프라인 개요

본 시스템의 데이터 파이프라인은 문서 업로드부터 검색 가능 상태까지의 전 처리 과정을 담당한다.  
핵심 설계 원칙은 **비동기 처리**, **변경 로그 기반 재시도**, **Atomic 버전 전환**이다.

```
[업로드 수신]
     │
     ▼
[메타데이터 저장 + 변경 로그 생성]
     │
     ▼ (비동기 처리 시작)
[Step 1: 텍스트 추출]
     │
     ▼
[Step 2: 청킹 (Chunking)]
     │
     ▼
[Step 3: 임베딩 생성]  ──── [임베딩 API (외부)]
     │
     ▼ (전체 청크 임베딩 완료 후)
[Step 4: Atomic 버전 전환]
     │
     ▼
[검색 가능 상태 (ACTIVE)]
```

---

## 2. 단계별 상세 설계

### Step 0: 업로드 수신 및 초기 등록

**목적**: 파일을 수신하고 DB에 처리 대기 상태로 등록한다.

**처리 흐름**:
```
1. 업로드 API가 파일과 메타데이터를 수신하고, 확장자를 DB 값(`pdf`/`txt`/`markdown`)으로 정규화한다.
2. 파일 형식 및 크기 검증 (PDF/TXT/Markdown, ≤ 100MB).
3. 임시 경로에 파일을 저장하며 SHA-256 해시를 계산한다. (중복 업로드 감지용)
4. 애플리케이션이 `version_id`를 먼저 생성하고 최종 경로를 `UPLOAD_DIR/<version_id>.<확장자>`로 결정한다.
5. DB 트랜잭션 시작:
   a. documents 테이블에 문서 레코드 INSERT (신규 문서인 경우)
   b. document_versions 테이블에 버전 레코드 INSERT (status = 'PENDING')
   c. change_log 테이블에 UPLOAD/UPDATE 이벤트 INSERT (status = 'PENDING')
6. DB 커밋 전에 임시 파일을 최종 경로로 원자적으로 이동한다. 파일 이동에 실패하면 DB를 롤백한다.
7. DB 트랜잭션 커밋. 커밋 이전에는 Worker가 `change_log`를 볼 수 없다.
8. 업로드 API가 즉시 문서 ID와 버전 ID를 반환한다.
9. Worker는 성공 또는 최종 실패 후 최종 파일을 정리한다.
```

**DB 변경**:
```sql
-- 트랜잭션 내 실행
INSERT INTO doc_search.documents (title, file_type, file_size_bytes, category, tags) ...;
INSERT INTO doc_search.document_versions (document_id, version_number, status, file_hash) 
    VALUES (..., 'PENDING', '<sha256>');
INSERT INTO doc_search.change_log (event_type, status, document_id, version_id)
    VALUES ('UPLOAD', 'PENDING', ..., ...);
```

---

### Step 1: 텍스트 추출 (Text Extraction)

**목적**: 원본 파일에서 텍스트와 구조 정보(페이지, 섹션)를 추출한다.

**Worker 처리 흐름**:
```
1. Worker가 `change_log`에서 `PENDING`이면서 `retry_count`에 따른 백오프 시간이 지난 항목을 `FOR UPDATE SKIP LOCKED` CTE로 선점한다.
2. 후보 조회와 `change_log`의 `PROCESSING` 갱신은 같은 트랜잭션에서 수행한다.
3. 같은 트랜잭션에서 `document_versions` 상태를 `PROCESSING`으로 업데이트한다.
4. 파일 형식에 따라 추출기를 선택한다:
   - PDF  → PyMuPDF (fitz): 페이지별 텍스트 + 페이지 번호
   - TXT  → 직접 읽기: 전체 텍스트
   - Markdown → mistune 파싱: 섹션 제목 + 텍스트
5. 추출된 텍스트 블록 목록을 메모리에 저장한다.
   형식: [{ text: str, page: int|None, section: str|None }, ...]
```

**오류 처리**:
- 추출 실패 시 change_log.retry_count를 증가시키고 status를 PENDING으로 되돌린다.
- max_retries 초과 시 DEAD_LETTER로 전환하고 알림을 발송한다.

---

### Step 2: 청킹 (Chunking)

**목적**: 추출된 텍스트를 검색에 최적화된 크기의 청크로 분할한다.

**청킹 알고리즘**:
```python
def chunk_text(text_blocks, chunk_size=512, overlap=50):
    """
    text_blocks: [{ text, page, section }, ...]
    chunk_size: 임베딩 모델 토큰 기준 최대 청크 크기 (`tiktoken` 사용)
    overlap: 인접 청크 간 중복 토큰 수
    """
    chunks = []
    buffer = ""
    buffer_tokens = 0
    current_page = None
    current_section = None
    chunk_index = 0

    for block in text_blocks:
        # 페이지/섹션 경계에서 강제 청크 분리 고려
        tokens = tokenize(block.text)
        
        for i in range(0, len(tokens), chunk_size - overlap):
            chunk_tokens = tokens[i : i + chunk_size]
            chunks.append({
                "index": chunk_index,
                "content": detokenize(chunk_tokens),
                "page_number": block.page,
                "section_title": block.section,
                "char_start": ...,
                "char_end": ...
            })
            chunk_index += 1

    return chunks
```

**DB 저장**:
```sql
-- 청크 일괄 INSERT (BATCH)
INSERT INTO doc_search.chunks 
    (version_id, document_id, chunk_index, content, page_number, section_title, char_start, char_end)
VALUES (...), (...), ...;

-- 버전 total_chunks 업데이트
UPDATE doc_search.document_versions 
SET total_chunks = <count>
WHERE id = <version_id>;
```

---

### Step 3: 임베딩 생성 (Embedding Generation)

**목적**: 각 청크 텍스트를 벡터로 변환하여 pgvector에 저장한다.

**처리 흐름**:
```
1. 임베딩이 완료되지 않은 청크 목록을 조회한다. (is_embedded = FALSE)
2. 청크를 배치(batch_size=100) 단위로 그룹화한다.
3. 배치별로 임베딩 API를 호출한다.
   - 호출: POST https://api.openai.com/v1/embeddings
   - 입력: { model: "text-embedding-3-small", input: [chunk_text, ...] }
   - 출력: { data: [{ embedding: [float, ...] }, ...] }
4. 반환된 벡터를 DB에 저장한다. (트랜잭션 단위: 배치 1개)
5. 저장 성공 시 해당 청크의 is_embedded를 TRUE로 업데이트한다.
6. embedded_chunks 카운터를 증가시킨다.
```

**DB 저장 (배치 트랜잭션)**:
```sql
BEGIN;

-- 임베딩 INSERT
INSERT INTO doc_search.embeddings 
    (chunk_id, version_id, document_id, embedding_model_id, vector)
VALUES 
    ('<chunk_id_1>', ..., ..., 1, '[0.12, -0.34, ...]'::vector),
    ('<chunk_id_2>', ..., ..., 1, '[0.56, 0.78, ...]'::vector),
    ...;

-- 청크 임베딩 완료 표시
UPDATE doc_search.chunks
SET is_embedded = TRUE, embedded_at = NOW()
WHERE id IN ('<chunk_id_1>', '<chunk_id_2>', ...);

-- 버전 embedded_chunks 카운터 업데이트
UPDATE doc_search.document_versions
SET embedded_chunks = embedded_chunks + <batch_size>
WHERE id = '<version_id>';

COMMIT;
```

**멱등성 보장**:
- 재처리 시 `is_embedded = FALSE`인 청크만 대상으로 하므로, 완료된 청크는 중복 처리되지 않는다.
- embeddings 테이블에 `chunk_id UNIQUE` 제약으로 중복 임베딩 INSERT를 방지한다.

---

### Step 4: Atomic 버전 전환 (Version Activation)

**목적**: 모든 청크의 임베딩이 완료된 후 해당 버전을 검색 대상으로 전환한다.

**처리 흐름**:
```
1. total_chunks == embedded_chunks 조건을 확인한다.
2. 조건 충족 시 doc_search.activate_version() 함수를 호출한다.
   - 기존 ACTIVE 버전 → ARCHIVED (원자적)
   - 신규 버전 → ACTIVE (원자적)
3. change_log의 최상위 이벤트를 COMPLETED로 업데이트한다.
```

**DB 실행**:
```sql
-- activate_version() 함수 내부에서 단일 트랜잭션으로 처리
SELECT doc_search.activate_version('<version_id>');
```

---

## 3. Worker 설계

### 3.1 Worker 루프

```python
import asyncio
import uuid

WORKER_ID = str(uuid.uuid4())  # 인스턴스별 고유 ID

async def worker_loop():
    while True:
        try:
            job = await fetch_pending_job()   # atomic claim: SELECT FOR UPDATE SKIP LOCKED
            if job is None:
                await asyncio.sleep(5)         # 대기
                continue

            await process_job(job)

        except Exception as e:
            await mark_job_failed(job, error=str(e))

async def fetch_pending_job():
    """변경 로그에서 PENDING 작업을 원자적으로 가져온다."""
    return await db.fetchrow("""
        UPDATE doc_search.change_log
        SET status = 'PROCESSING',
            worker_id = $1,
            locked_at = NOW(),
            updated_at = NOW()
        WHERE id = (
            SELECT id FROM doc_search.change_log
            WHERE status = 'PENDING'
              AND (
                  retry_count = 0 OR
                  updated_at <= NOW() - CASE retry_count
                      WHEN 1 THEN INTERVAL '30 seconds'
                      WHEN 2 THEN INTERVAL '120 seconds'
                      ELSE INTERVAL '300 seconds'
                  END
              )
            ORDER BY created_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING *
    """, WORKER_ID)
```

### 3.2 재시도 로직

```python
async def mark_job_failed(job, error: str):
    new_retry_count = job['retry_count'] + 1
    
    if new_retry_count >= job['max_retries']:
        # 최대 재시도 초과 → DEAD_LETTER
        new_status = 'DEAD_LETTER'
        await send_alert(job, error)
    else:
        # 재시도 허용 → PENDING으로 되돌림
        new_status = 'PENDING'

    await db.execute("""
        UPDATE doc_search.change_log
        SET status = $1,
            retry_count = $2,
            error_message = $3,
            worker_id = NULL,
            locked_at = NULL,
            updated_at = NOW()
        WHERE id = $4 AND worker_id = $5
    """, new_status, new_retry_count, error, job['id'], job['worker_id'])
```

---

## 4. 장애 시나리오별 복구 전략

### 시나리오 A: Worker 프로세스 비정상 종료

```
상황: 임베딩 처리 중 Worker 프로세스가 kill됨

1. change_log 항목이 PROCESSING 상태로 고착됨 (locked_at 존재)
2. 감시 프로세스(또는 Worker 재시작)가 PROCESSING 상태이지만
   locked_at이 타임아웃(예: 10분)을 초과한 항목을 PENDING으로 되돌림:
   
   UPDATE doc_search.change_log
   SET status = 'PENDING', worker_id = NULL,
       locked_at = NULL, updated_at = NOW()
   WHERE status = 'PROCESSING'
     AND locked_at < NOW() - INTERVAL '10 minutes';
   
3. Worker 재시작 시 PENDING 항목을 자동으로 감지하고 재처리
4. 이미 임베딩된 청크(is_embedded = TRUE)는 건너뜀 → 멱등성 보장
```

### 시나리오 B: 임베딩 API 일시적 장애

```
상황: 배치 단위 임베딩 API 호출 실패

1. 해당 배치의 청크는 is_embedded = FALSE로 유지
2. change_log.retry_count 증가, updated_at 갱신, status = PENDING
3. 지수 백오프(Exponential Backoff)로 재시도:
   - 1차 재시도: 30초 후
   - 2차 재시도: 120초 후
   - 3차 재시도: 300초 후
4. `retry_count`가 `max_retries`에 도달하면 DEAD_LETTER → 알림 발송
```

### 시나리오 C: DB Primary 노드 장애 (Patroni Failover)

```
상황: OpenSQL Primary 노드 장애 → Patroni가 30초 내 Failover 수행

1. 진행 중이던 트랜잭션은 실패 (연결 끊김)
2. change_log 항목은 PENDING 또는 PROCESSING 상태로 보존 (WAL 복제로 Standby에 반영)
3. Failover 완료 후 새 Primary에서 Worker 재시작
4. PROCESSING 타임아웃 된 항목은 시나리오 A와 동일하게 처리
```

### 시나리오 D: pgvector 인덱스 손상

```
상황: HNSW 인덱스 파일 손상으로 검색 불가

1. 기존 인덱스 삭제:
   DROP INDEX doc_search.idx_embeddings_hnsw;
   
2. 저장된 벡터 데이터 기반으로 재구축:
   CREATE INDEX idx_embeddings_hnsw
   ON doc_search.embeddings
   USING hnsw (vector vector_cosine_ops)
   WITH (m = 16, ef_construction = 64);
   
   (주의: 재구축 중에는 정확도가 낮은 순차 스캔으로 대체됨)
   
3. 재구축 완료 후 정상 서비스 재개
```

---

## 5. 파이프라인 흐름도 (전체)

```
[문서 업로드 API]
        │
        │ ① 파일 검증 + DB 등록 (동기)
        ▼
[documents + document_versions (PENDING) + change_log (PENDING)]
        │
        │ ② Worker가 change_log 감지 (비동기)
        ▼
[Worker: SELECT FOR UPDATE SKIP LOCKED]
        │
        ├── ③ 텍스트 추출 (PDF/TXT/MD)
        │        │
        │        ▼
        ├── ④ 청킹 → chunks 테이블 INSERT
        │        │
        │        ▼
        ├── ⑤ 임베딩 생성 (배치)
        │        │
        │        ├── 임베딩 API 호출 (외부)
        │        │
        │        └── embeddings INSERT + chunks.is_embedded = TRUE
        │
        │ ⑥ total_chunks == embedded_chunks ?
        │        │
        │       YES
        │        ▼
        └── ⑦ activate_version() [단일 트랜잭션]
                 │  기존 ACTIVE → ARCHIVED
                 │  신규 버전   → ACTIVE
                 │  change_log  → COMPLETED
                 ▼
        [검색 가능 상태]
                 │
                 ▼
        [MCP search_documents 호출]
                 │
                 ├── 질의 임베딩 → pgvector ANN 검색
                 │
                 └── 결과 반환: 청크 텍스트, 문서명, 버전, 페이지, 유사도
```

---

## 6. 설정 파라미터 목록

| 파라미터 | 기본값 | 설명 |
|---------|--------|------|
| `CHUNK_SIZE` | 512 | 청크 토큰 크기 |
| `CHUNK_OVERLAP` | 50 | 청크 간 중복 토큰 수 |
| `EMBEDDING_MODEL` | text-embedding-3-small | 임베딩 모델명 |
| `EMBEDDING_DIMENSIONS` | 1536 | 벡터 차원수 |
| `EMBEDDING_BATCH_SIZE` | 100 | 배치당 청크 수 |
| `MAX_RETRIES` | 3 | 최대 재시도 횟수 |
| `WORKER_LOCK_TIMEOUT_MINUTES` | 10 | Worker 락 타임아웃 |
| `WORKER_POLL_INTERVAL_SECONDS` | 5 | PENDING 항목 조회 주기 |
| `HNSW_M` | 16 | HNSW 인덱스 연결 수 |
| `HNSW_EF_CONSTRUCTION` | 64 | HNSW 인덱스 구축 품질 |
| `HNSW_EF_SEARCH` | 40 | HNSW 검색 품질 |
| `MAX_FILE_SIZE_MB` | 100 | 업로드 최대 파일 크기 |
