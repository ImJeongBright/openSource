# 데모 시나리오 (Demo Scenario)

> **구현 상태 주의:** 현재 저장소의 `mcp/server.py` 스켈레톤은 stdio transport이고, FastAPI/MCP 기능도 아직 `NotImplementedError` 상태입니다. 아래 HTTP `curl` 예시는 HTTP transport adapter와 해당 API 구현을 완료한 뒤의 목표 시나리오입니다. 현재 Phase 8 검증은 MCP Inspector 또는 Claude Desktop 연동을 기준으로 합니다.

**프로젝트명**: OpenSQL 기반 AI 문서 검색 및 버전 관리 시스템  
**문서 버전**: v1.0  
**작성일**: 2026-08-05  
**목적**: 심사 및 발표를 위한 End-to-End 시연 시나리오 및 장애 복구 실연 가이드

---

## 1. 데모 개요

본 데모는 다음 세 가지 핵심 가치를 심사자가 직접 눈으로 확인할 수 있도록 구성한다.

| # | 시연 테마 | 핵심 메시지 |
|---|----------|------------|
| Demo 1 | End-to-End 문서 처리 흐름 | "업로드 하나로 AI 검색까지 자동 완성" |
| Demo 2 | 버전 전환 Atomic 보장 | "업데이트 중에도 검색 결과는 흔들리지 않는다" |
| Demo 3 | 장애 복구 실연 | "DB가 죽어도 데이터는 살아있다" |
| Demo 4 | tibero_fdw 연동 (선택) | "기존 티베로 자산을 AI 검색으로 확장" |

---

## 2. 사전 준비 (Pre-requisites)

```bash
# OpenSQL 클러스터 상태 확인
curl http://localhost:8008/cluster   # Patroni API

# pgvector 확장 활성화 확인
psql -U app_user -d doc_search -c "SELECT extname FROM pg_extension WHERE extname = 'vector';"

# MCP 서버 기동 확인
curl http://localhost:8080/health

# Worker 프로세스 실행 확인
systemctl status opensql-worker
```

### 데모용 샘플 문서 준비

| 파일명 | 설명 | 형식 |
|--------|------|------|
| `sample_v1.pdf` | 사내 보안 정책 문서 초안 (5페이지) | PDF |
| `sample_v2.pdf` | 사내 보안 정책 문서 개정본 (7페이지) | PDF |
| `opensql_manual.md` | OpenSQL 운영 매뉴얼 | Markdown |
| `faq.txt` | 자주 묻는 질문 문서 | TXT |

---

## 3. Demo 1: End-to-End 문서 처리 흐름

**시연 목표**: 문서 업로드 → 자동 임베딩 → MCP 검색까지 원스톱으로 동작함을 보여준다.

### 3.1 문서 업로드

```bash
# PDF 문서 업로드
curl -X POST http://localhost:8000/api/documents \
  -F "file=@sample_v1.pdf" \
  -F "title=사내 보안 정책" \
  -F "category=보안"

# 응답 예시
{
  "document_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "version_id": "3a8b9c2d-...",
  "status": "PENDING",
  "message": "문서가 접수되었습니다. 처리 중..."
}
```

### 3.2 처리 진행 상황 모니터링

```sql
-- DB에서 실시간 처리 현황 확인
SELECT document_title, version_number, version_status,
       total_chunks, embedded_chunks, embedding_progress_pct
FROM doc_search.processing_status
WHERE document_title = '사내 보안 정책';

-- 기대 출력 (처리 중)
-- 사내 보안 정책 | 1 | PROCESSING | 42 | 30 | 71.4%

-- 기대 출력 (완료)
-- 사내 보안 정책 | 1 | ACTIVE     | 42 | 42 | 100.0%
```

### 3.3 MCP 의미 검색

```bash
# MCP search_documents 도구 호출
curl -X POST http://localhost:8080/mcp/tools/search_documents \
  -H "Content-Type: application/json" \
  -d '{
    "query": "개인정보 보호를 위한 접근 제어 정책은 무엇인가요?",
    "top_k": 3
  }'
```

**기대 결과**:
```json
{
  "results": [
    {
      "chunk_text": "3.2 접근 제어 정책\n모든 직원은 업무에 필요한 최소한의 권한만을 부여받아야 하며...",
      "document_title": "사내 보안 정책",
      "version_number": 1,
      "page_number": 3,
      "section_title": "3.2 접근 제어 정책",
      "similarity": 0.923
    },
    ...
  ]
}
```

> **심사 포인트**: 결과에 `document_title`, `version_number`, `page_number`, `section_title`이 모두 포함되어 있어 답변 근거 추적이 가능함을 강조한다.

---

## 4. Demo 2: Atomic 버전 전환

**시연 목표**: 문서 업데이트 중에도 검색 결과가 일관성을 유지함을 보여준다.

### 4.1 시나리오 설정

```bash
# 터미널 A: 지속적으로 검색 수행 (1초 간격)
watch -n 1 'curl -s -X POST http://localhost:8080/mcp/tools/search_documents \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"접근 제어 정책\", \"top_k\": 1}" | python3 -m json.tool'
```

```bash
# 터미널 B: 새 버전 업로드 (개정본 7페이지)
curl -X PUT http://localhost:8000/api/documents/f47ac10b-... \
  -F "file=@sample_v2.pdf"
```

### 4.2 관찰 포인트

```
터미널 A 출력 변화:
  - 업로드 직후 ~ 임베딩 완료 전: version_number = 1 (기존 버전 유지)  ← 핵심!
  - 임베딩 100% 완료 후:          version_number = 2 (새 버전으로 전환)

중간 상태(일부만 임베딩된 버전 2)가 검색 결과에 절대 노출되지 않음을 확인
```

### 4.3 DB로 전환 시점 확인

```sql
-- 버전 전환 트랜잭션 기록 확인
SELECT event_type, status, created_at
FROM doc_search.change_log
WHERE document_id = 'f47ac10b-...'
ORDER BY created_at;

-- VERSION_SWITCH 이벤트 발생 시각 = 검색 결과가 v2로 바뀐 시각과 일치
```

---

## 5. Demo 3: 장애 복구 실연

**시연 목표**: Worker 또는 DB 노드 장애 후 데이터 유실 없이 자동 복구됨을 보여준다.

### 5.1 시나리오 A: Worker 프로세스 장애

```bash
# 1단계: 대용량 문서 업로드 (처리에 약 30초 소요 예상)
curl -X POST http://localhost:8000/api/documents \
  -F "file=@opensql_manual.md" \
  -F "title=OpenSQL 운영 매뉴얼"

# 2단계: 처리 진행 중 확인 (임베딩 약 50% 진행 시)
psql -U app_user -d doc_search \
  -c "SELECT embedded_chunks, total_chunks FROM doc_search.document_versions WHERE status='PROCESSING';"

# 3단계: Worker 강제 종료 (장애 시뮬레이션)
sudo systemctl stop opensql-worker
echo "Worker 종료됨 - $(date)"

# 4단계: change_log 상태 확인 (PROCESSING 고착 상태)
psql -U app_user -d doc_search \
  -c "SELECT id, status, worker_id, locked_at FROM doc_search.change_log WHERE status='PROCESSING';"

# 5단계: Worker 재시작
sleep 5
sudo systemctl start opensql-worker
echo "Worker 재시작됨 - $(date)"

# 6단계: 자동 복구 완료 확인
watch -n 2 'psql -U app_user -d doc_search -c \
  "SELECT embedded_chunks, total_chunks, status FROM doc_search.document_versions WHERE status IN ('"'"'PROCESSING'"'"', '"'"'ACTIVE'"'"');"'
```

**기대 결과**:
```
Worker 재시작 후 약 10초 내에 PROCESSING 잠금 해제
→ 이미 완료된 청크(is_embedded=TRUE)는 건너뜀 (멱등성)
→ 나머지 청크만 이어서 임베딩
→ 100% 완료 후 ACTIVE 전환
→ 데이터 유실 0건 확인
```

### 5.2 시나리오 C: DB Primary 노드 장애 (Patroni Failover)

```bash
# 1단계: 현재 Primary 노드 확인
curl http://localhost:8008/cluster | python3 -m json.tool | grep -A2 '"role"'

# 2단계: Primary 노드 강제 중단 (장애 시뮬레이션)
sudo systemctl stop patroni   # Primary 노드에서 실행
FAILOVER_START=$(date +%s)
echo "Primary 노드 장애 시각: $(date)"

# 3단계: 다른 노드에서 Patroni 상태 모니터링
watch -n 1 'curl -s http://localhost:8008/cluster | python3 -m json.tool | grep -E "role|state"'

# 4단계: Failover 완료 시간 측정
# 새 Primary가 선출되면:
FAILOVER_END=$(date +%s)
echo "Failover 완료까지 소요 시간: $((FAILOVER_END - FAILOVER_START))초"
# 목표: ≤ 30초

# 5단계: 새 Primary에서 검색 정상 동작 확인
curl -X POST http://localhost:8080/mcp/tools/search_documents \
  -d '{"query": "접근 제어 정책", "top_k": 1}'
```

---

## 6. Demo 4: tibero_fdw 연동 (선택 데모)

**시연 목표**: 기존 티베로 DB에 있는 문서 데이터를 OpenSQL로 끌어와 AI 검색으로 확장함을 보여준다.

> **시연 조건**: 티베로 DB 인스턴스가 사전에 구성되어 있어야 한다.

```sql
-- 1단계: tibero_fdw 확장 활성화
CREATE EXTENSION IF NOT EXISTS tibero_fdw;

-- 2단계: 티베로 서버 연결 설정
CREATE SERVER tibero_server
    FOREIGN DATA WRAPPER tibero_fdw
    OPTIONS (host 'tibero-host', port '8629', dbname 'tibero');

CREATE USER MAPPING FOR app_user
    SERVER tibero_server
    OPTIONS (username 'tibero', password 'password');

-- 3단계: 티베로 문서 테이블을 외부 테이블로 연결
CREATE FOREIGN TABLE tibero_documents (
    doc_id    INTEGER,
    title     VARCHAR(500),
    content   TEXT,
    created_at TIMESTAMP
)
SERVER tibero_server
OPTIONS (schema 'DOCMGR', table 'TB_DOCUMENTS');

-- 4단계: 티베로 문서를 조회하여 OpenSQL 파이프라인으로 전달
SELECT doc_id, title, content FROM tibero_documents LIMIT 5;
```

**이후 흐름**: 조회된 문서를 업로드 API로 전달 → 동일한 파이프라인으로 임베딩 → MCP 검색에서 티베로 원본 문서도 검색 가능

> **심사 포인트**: 티맥스 제품군(Tibero + OpenSQL) 간 생태계 통합 사례를 제시함으로써 자사 기술 활용도를 극대화했음을 강조한다.

---

## 7. 성능 측정 결과 기록 템플릿

데모 완료 후 아래 표를 채워 제출 자료에 포함한다.

| 지표 | 목표 | 실측 결과 | 테스트 조건 |
|------|------|----------|------------|
| 검색 p95 응답시간 | ≤ 500ms | ___ ms | locust, 동시 50 사용자, 5분 |
| MCP 도구 성공률 | ≥ 99.5% | ___% | 1,000회 반복 호출 |
| Worker 장애 복구 시간 | - | ___ 초 | 처리 중 강제 종료 후 재시작 |
| Patroni Failover 시간 | ≤ 30초 | ___ 초 | Primary systemctl stop |
| 버전 전환 중 불일치 건수 | 0건 | ___ 건 | v2 임베딩 중 1,000회 검색 |

### locust 부하 테스트 실행 명령

```bash
# locustfile.py 작성 후
locust -f tests/load/locustfile.py \
  --host http://localhost:8080 \
  --users 50 \
  --spawn-rate 5 \
  --run-time 5m \
  --headless \
  --csv results/load_test_$(date +%Y%m%d)
```

---

## 8. 데모 진행 타임라인 (15분 기준)

| 시간 | 내용 |
|------|------|
| 00:00 ~ 01:00 | 시스템 구성 소개 (OpenSQL HA 클러스터, 파이프라인, MCP 서버) |
| 01:00 ~ 04:00 | Demo 1: 문서 업로드 → 자동 임베딩 → MCP 검색 |
| 04:00 ~ 07:00 | Demo 2: 버전 업데이트 중 Atomic 전환 확인 |
| 07:00 ~ 11:00 | Demo 3: Worker 장애 → 자동 복구 (시나리오 A) |
| 11:00 ~ 13:00 | Demo 3: DB Failover → 30초 내 복구 (시나리오 C) |
| 13:00 ~ 14:00 | Demo 4: tibero_fdw 연동 (선택) |
| 14:00 ~ 15:00 | 성능 측정 결과 발표 및 Q&A |
