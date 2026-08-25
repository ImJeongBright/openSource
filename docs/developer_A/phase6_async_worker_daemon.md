# Phase 6: 비동기 Worker 데몬 구현 상세

## 1. 개요
본 단계에서는 데이터베이스의 `change_log`를 지속적으로 모니터링하여, 파이프라인 처리가 필요한 문서 작업을 안전하게 선점하고 상태를 전이시키는 워커(Worker) 데몬을 구현합니다. `change_log.status`는 작업 상태이고 `document_versions.status`는 버전 상태이므로 두 상태를 혼용하지 않습니다.

## 2. 세부 구현 목표

### 2.1. 동시성 제어 적용 (`SELECT FOR UPDATE SKIP LOCKED`)
- **목적**: 여러 대의 워커 프로세스나 컨테이너가 동시에 실행되더라도, 동일한 작업을 중복으로 가져가지 않도록 보장합니다.
- **방식**: 후보 조회와 `PROCESSING` 갱신을 반드시 같은 DB 트랜잭션에서 수행합니다. `SELECT ... FOR UPDATE`만 실행한 뒤 트랜잭션을 끝내면 락이 풀려 중복 선점이 발생할 수 있습니다.
- **핵심 쿼리 적용**:
  ```sql
  WITH candidate AS (
      SELECT id
      FROM doc_search.change_log
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
      FOR UPDATE SKIP LOCKED
      LIMIT 1
  )
  UPDATE doc_search.change_log AS cl
  SET status = 'PROCESSING', worker_id = $1,
      locked_at = NOW(), updated_at = NOW()
  FROM candidate
  WHERE cl.id = candidate.id
  RETURNING cl.*;
  ```
  이 쿼리는 즉시 락을 획득할 수 있는 가장 오래된 `PENDING` 작업 1개를 선점합니다. 반환된 행의 `document_versions`도 같은 트랜잭션에서 `PENDING` → `PROCESSING`으로 변경합니다.

### 2.2. 상태 전이 및 재시도 로직 (`src/worker/daemon.py`)

#### 워크플로우 및 상태 전이
- **상태 관리**: 워커가 작업을 선점하면 `change_log`를 `PROCESSING`으로, 해당 버전을 `PROCESSING`으로 업데이트합니다.
  - 작업: `PENDING` ➔ `PROCESSING` ➔ `COMPLETED` (성공 시) 또는 `DEAD_LETTER` (재시도 소진 시)
  - 버전: `PENDING` ➔ `PROCESSING` ➔ `ACTIVE` (성공 시) 또는 `FAILED` (최종 실패 시)
- **로깅**: 모든 상태 전이는 로그 파일 및 필요 시 `change_log` 테이블 이력으로 상세히 남겨야 합니다. (문서 ID, 버전 ID, 작업 시작/종료 시간 포함)

#### Exponential Backoff (지수적 재시도 백오프)
- **장애 대응**: 임베딩 API 호출 타임아웃, 외부 서비스 일시 장애, DB 접속 지연 등의 일시적 오류 발생 시 즉각 실패 처리하지 않고 재시도합니다.
- **간격 설정**: 프로젝트 정책상 30초, 120초, 300초의 재시도 일정을 사용합니다. B 담당 `change_log` 스키마를 변경하지 않기 위해 마지막 상태 변경 시각(`updated_at`)과 `retry_count`로 다음 실행 가능 시각을 계산합니다.
- **Dead Letter Queue (DLQ)**: 최대 재시도 횟수를 초과한 작업은 `DEAD_LETTER`로 마킹하고 버전은 `FAILED`로 마킹하여 무한 루프를 방지하고 관리자가 수동으로 개입할 수 있도록 합니다.

#### 좀비 작업 복구 (Sweeper 로직)
- **문제 정의**: 워커가 작업을 `PROCESSING` 상태로 변경한 직후 예상치 못하게 죽거나(OOM, 강제 종료 등), 프로세스가 멈춘 경우 해당 작업이 영원히 `PROCESSING` 상태로 갇히는 좀비 현상을 방지해야 합니다.
- **복구 로직**: 
  - 상태가 `PROCESSING`으로 변경되었으나 `locked_at` 갱신이 일정 시간(예: 10분) 멈춘 작업은 멈춘 작업으로 간주합니다.
  - 정상적으로 10분 이상 걸릴 수 있는 작업을 오인하지 않도록 Worker가 처리 중 주기적으로 `locked_at`을 heartbeat로 갱신합니다.
  - 스위퍼는 `status = 'PROCESSING' AND locked_at < ...` 조건으로만 복구하고, `worker_id`와 lease 정보를 초기화하여 다시 `PENDING`으로 돌립니다.
  - 완료/실패 갱신에는 `WHERE id = $1 AND worker_id = $2`를 사용하여, 이미 lease를 잃은 오래된 Worker가 새 Worker의 상태를 덮어쓰지 못하게 합니다.

## 3. 테스트 및 모니터링
- 유닛 및 통합 테스트 시 워커 간 동시성 충돌이 없는지 집중 점검합니다.
- 스위퍼 로직이 실제 좀비 작업을 정상적으로 찾아내는지, 복구된 작업이 다시 정상 워커에 의해 처리되는지 검증합니다.
- 재시도 시각 이전의 작업이 선점되지 않는지, 최대 재시도 초과 시 버전도 `FAILED`로 남고 알림 대상이 되는지 검증합니다.
