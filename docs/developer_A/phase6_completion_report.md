# Phase 6 구현 완료 보고서

**담당**: Developer A
**단계**: 비동기 Worker 데몬 및 작업 상태 머신
**완료일**: 2026-08-26
**작업 브랜치**: `feat/worker`
**통합 브랜치**: `develop` → `feat/api`

## 1. 완료 요약

`change_log`를 안전하게 선점하고, Worker 장애와 일시적 오류에 대응할 수 있는 Phase 6의 작업 수명주기 관리 기능을 구현했습니다.

- `SELECT ... FOR UPDATE SKIP LOCKED` 기반 원자적 작업 선점
- `change_log`와 `document_versions`의 트랜잭션 단위 상태 전이
- Worker별 UUID lease 식별자 및 heartbeat
- 30초 → 120초 → 300초 재시도 백오프
- 최대 재시도 초과 시 `DEAD_LETTER` 및 버전 `FAILED` 전환
- 만료된 `PROCESSING` 작업을 `PENDING`으로 복구하는 sweeper
- lease 소유자 검증을 포함한 완료·실패 갱신
- 폴링 주기와 graceful stop을 지원하는 Worker 루프

구현 파일은 [src/worker/daemon.py](../../src/worker/daemon.py)입니다.

## 2. 구현 내용

### 2.1 원자적 작업 선점

`Worker.claim_job()`은 하나의 DB 트랜잭션에서 다음 작업을 수행합니다.

1. `PENDING` 상태이면서 재시도 가능 시각이 지난 작업을 조회합니다.
2. `FOR UPDATE SKIP LOCKED`로 다른 Worker가 잠근 작업을 건너뜁니다.
3. 가장 오래된 작업 하나를 `PROCESSING`으로 전환합니다.
4. `worker_id`, `locked_at`, `updated_at`을 기록합니다.
5. 연결된 버전을 `PENDING`에서 `PROCESSING`으로 전환합니다.

버전 상태 갱신에 실패하면 전체 트랜잭션이 rollback되어 작업만 선점된 상태로 남지 않습니다.

### 2.2 Heartbeat 및 lease 보호

처리 중인 Worker는 `locked_at`을 주기적으로 갱신합니다. Heartbeat와 완료·실패 갱신에는 다음 조건을 함께 사용합니다.

```sql
WHERE id = $1
  AND status = 'PROCESSING'
  AND worker_id = $2
```

따라서 lease를 잃은 이전 Worker가 새 Worker의 상태를 덮어쓸 수 없습니다.

### 2.3 재시도 및 Dead Letter 처리

작업 실패 시 `retry_count`를 증가시키고 `updated_at`을 기준으로 다음 선점 시각을 계산합니다.

| `retry_count` | 다음 재시도 대기 시간 |
|---:|---:|
| 0 | 즉시 |
| 1 | 30초 |
| 2 | 120초 |
| 3 이상 | 300초 |

`max_retries`에 도달하면:

- `change_log.status` → `DEAD_LETTER`
- `document_versions.status` → `FAILED`
- 오류 메시지와 오류 유형을 기록

재시도 가능한 오류는 작업과 버전을 각각 `PENDING`으로 되돌립니다.

### 2.4 Zombie 작업 복구

`recover_stale_jobs()`는 `locked_at`이 `WORKER_LOCK_TIMEOUT_MINUTES`보다 오래된 `PROCESSING` 작업만 대상으로 합니다.

- 작업의 `worker_id`와 `locked_at` 초기화
- `change_log.status` → `PENDING`
- 해당 버전이 아직 `PROCESSING`이면 `PENDING`으로 복구

정상 heartbeat가 갱신되는 작업은 sweeper 대상이 되지 않습니다.

### 2.5 Worker 실행 루프

Worker는 다음 순서로 반복 실행됩니다.

```text
stale lease 복구
    → 작업 선점
    → heartbeat 시작
    → 주입된 job handler 실행
    → 성공 시 COMPLETED / 실패 시 재시도 또는 DEAD_LETTER
```

폴링 간격은 `WORKER_POLL_INTERVAL_SECONDS`, lease 만료 시간은 `WORKER_LOCK_TIMEOUT_MINUTES` 설정을 사용합니다.

Phase 7에서 실제 문서 처리 파이프라인을 `job_handler` 경계에 연결할 수 있도록 분리했으며, 이후 [Phase 7 완료 보고서](phase7_completion_report.md)에서 해당 연결을 완료했습니다.

## 3. 테스트 및 검증

### 3.1 추가 테스트

[tests/worker/test_daemon.py](../../tests/worker/test_daemon.py)에 9개 테스트를 추가했습니다.

- 원자적 작업 선점과 버전 상태 갱신
- 처리 대상이 없을 때의 no-op
- 현재 Worker lease를 가진 경우의 heartbeat
- lease를 잃은 경우의 갱신 거부
- 일시적 오류의 `PENDING` 재시도 전환
- 최종 오류의 `DEAD_LETTER`/`FAILED` 전환
- 만료 작업과 버전의 복구
- 성공·실패 handler의 상태 처리
- heartbeat 간격 설정

### 3.2 실행 결과

```text
95 passed, 1 skipped in 2.70s
All checks passed!
```

실행 명령:

```bash
TIKTOKEN_CACHE_DIR=/tmp/opensql-tiktoken-cache .venv/bin/pytest -q
.venv/bin/ruff check .
```

`skipped` 1건은 Ollama 실서비스가 필요한 통합 테스트이며, `RUN_OLLAMA_INTEGRATION=1`일 때만 실행됩니다.

## 4. 실행 환경

- Rocky Linux 9.8
- Python 3.11.16
- 프로젝트 가상환경: `.venv`
- Python 3.9 시스템 인터프리터는 운영 도구 호환성을 위해 삭제하지 않았습니다.
- 기존 Python 3.9 프로젝트 환경은 `/home/rocky/opensql-doc-search-py39-venv-backup`에 보존했습니다.

`opensql-worker.service`의 Python 경로도 새 `.venv`를 사용하도록 수정했습니다.

## 5. Git 통합 상태

```text
2a1c4a3 feat(worker): implement lease-based worker state machine
95eb89f merge: integrate worker state machine
```

로컬에서는 다음 통합을 완료했습니다.

- `feat/worker` → `develop`
- `develop` → `feat/api` (fast-forward)

현재 `feat/api`에서 Phase 7을 시작할 수 있습니다.

단, GitHub HTTPS 인증 정보가 이 환경에 설정되어 있지 않아 원격 push는 아직 완료되지 않았습니다. 인증 후 다음 명령을 실행해야 원격 브랜치까지 동기화됩니다.

```bash
git push origin feat/worker
git push origin develop
git push origin feat/api
```

## 6. 후속 단계

Phase 6 완료 시 이월했던 문서 처리 파이프라인 연결과 원자적 버전 활성화는 Phase 7에서 완료했습니다. 구현 및 검증 내용은 [Phase 7 완료 보고서](phase7_completion_report.md)를 참조합니다.
