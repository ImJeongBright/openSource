# 개발자 A 상세 Phase 구현 계획 (Phase 4, 6, 7, 9)

## Phase 4: 텍스트 추출 및 청킹(Chunking) 구현
**목표:** 파일로부터 텍스트를 추출하고 의미 단위의 청크로 분할 (파이프라인의 시작점)

1. **텍스트 추출기 개발** (`src/pipeline/extractor.py`)
   - **PDF 추출**: `PyMuPDF (fitz)`를 사용하여 페이지 단위로 텍스트를 추출한다. 이미지 전용 페이지는 건너뛰고 OCR 미지원 로그를 남긴다.
   - **TXT 추출**: 파일을 명시적 인코딩 정책으로 읽고 `TextBlock(text=..., page=None, section=None)`으로 반환한다.
   - **Markdown 추출**: `mistune` 파서를 활용하여 Header(H1, H2, H3) 구조와 상위 섹션 컨텍스트를 유지한다.
   - **출력 포맷 보장**: 공유 모델의 `TextBlock(text, page, section)`과 `ChunkData` 필드를 그대로 사용한다.

2. **청킹 알고리즘 적용** (`src/pipeline/chunker.py`)
   - **슬라이딩 윈도우**: `chunk_size=512`, `overlap=50` 토큰 기준으로 청킹한다. 버전별 설정은 Worker가 DB에서 읽어 함수 인자로 전달한다.
   - **경계 보존**: 문단·줄바꿈·문장 경계를 우선 사용하되, 모든 청크는 최대 토큰 수를 넘지 않도록 한다.
   - **청크 무결성 검증**: 길이, overlap, 순번, 페이지/섹션 메타데이터 상속, 극단적으로 긴 단어와 특수문자 입력을 `pytest`로 검증한다.

---

## Phase 6: 비동기 Worker 데몬 구현
**목표:** DB의 `change_log`를 모니터링하며 작업을 안전하게 선점하고 상태를 전이

1. **동시성 제어 적용** (`SELECT FOR UPDATE SKIP LOCKED`)
   - 동시 워커의 중복 선점을 막기 위해 후보 조회와 `PROCESSING` 갱신을 하나의 트랜잭션으로 묶는다.
   - `retry_count`에 따른 백오프 시간이 지나기 전에는 선점하지 않으며, 처리 중에는 lease heartbeat를 갱신한다.
   - 구체적인 CTE 선점 쿼리는 `phase6_async_worker_daemon.md`의 패턴을 기준으로 한다.

2. **상태 전이 및 재시도 로직** (`src/worker/daemon.py`)
   - **상태 전이**: `change_log`는 `PENDING` → `PROCESSING` → `COMPLETED`/`DEAD_LETTER`, 버전은 `PENDING` → `PROCESSING` → `ACTIVE`/`FAILED`로 분리해 관리한다.
   - **Backoff**: 임베딩 API 타임아웃이나 DB 일시 오류 시 30초 → 120초 → 300초 일정으로 재시도하고 `updated_at`/`retry_count`로 다음 실행 가능 시각을 계산한다. 최대 재시도 초과 시 `DEAD_LETTER`로 이동한다.
   - **좀비 작업 복구**: heartbeat가 없는 `PROCESSING` 작업만 lease timeout 후 다시 `PENDING`으로 복구하는 스위퍼를 구현한다. 완료/실패 갱신에는 lease 소유자 검증을 포함한다.

---

## Phase 7: Atomic 버전 전환 및 파이프라인 통합
**목표:** 개별 모듈들을 하나의 완전한 파이프라인으로 연결하고 정합성을 보장

1. **저장 함수 연동** (`src/pipeline/versioner.py`)
   - 워커 파이프라인의 최종 단계에서 `chunks`/`embeddings`를 재집계하여 누락 벡터가 없는지 검증한다 (`total_chunks == embedded_chunks`).
   - 검증이 완료되면 `activate_version()`을 호출한다. 함수는 대상 문서 행을 잠근 뒤 기존 `ACTIVE` 버전을 `ARCHIVED`, 새 버전을 `ACTIVE`로 원자적으로 전환한다.

2. **멱등성 검증 체계 구축
   - 재실행 시 `chunks.is_embedded`와 `embeddings.chunk_id`를 함께 확인해 이미 저장된 청크는 건너뛰고 나머지만 재처리한다.
   - 임베딩 INSERT, `is_embedded` 갱신, `embedded_chunks` 증가를 하나의 배치 트랜잭션으로 처리하고 실제 신규 저장 건수만 카운터에 반영한다.

---

## Phase 9: 통합 테스트 및 데모 시나리오 준비 (인프라 중심)
**목표:** 시스템의 견고함과 장애 대응 능력을 증명

1. **하드코어 테스트 시나리오 구성**
   - 격리된 데모 워커를 강제 종료하고 lease timeout과 스위퍼가 작업을 재개하는지 시연한다.
   - Patroni HA 환경이 준비된 경우에만 Primary 장애와 Failover를 시연한다. 그 외 환경에서는 DB 연결 오류 후 트랜잭션 롤백·재처리로 대체한다.
   - 같은 문서의 버전 전환 동시성, 재시도 시각 이전 선점 방지, 중복 임베딩 방지를 통합 테스트한다.

2. **퍼포먼스 벤치마크 지표 추출**
   - 추출, 청킹, 배치 저장, 인덱싱 단계의 소요 시간·처리량·재시도 횟수를 구조화 로그로 기록한다.
   - 실제 측정값만 성능 리포트에 반영하며, 대시보드는 별도 구현된 경우에만 사용한다.
