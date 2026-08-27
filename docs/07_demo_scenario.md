# Phase 9 통합 데모 시나리오

현재 API, Worker, 로컬 Qwen3 임베딩, OpenSQL 벡터 검색과 MCP stdio 서버가 구현되어
있습니다. HTTP 8080 MCP 엔드포인트는 제공하지 않으며, AI 클라이언트가 `mcp/server.py`를
자식 프로세스로 실행합니다.

## 1. 사전 확인

```bash
systemctl is-active patroni opensql-etcd
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
.venv/bin/python mcp/server.py --check
```

`/ready`가 성공하려면 서버에서 `.env`의 `EMBEDDING_BASE_URL`로 Qwen3 Embedding 모델에
접근할 수 있어야 합니다. 맥 Ollama를 사용할 때는 데모 전에 SSH 역방향 포워딩을 준비합니다.

## 2. 자동 데모

다음 명령은 보안 정책 V1과 V2를 업로드하고, V2 문서에 새 버전을 추가하여 처리 중에도
기존 ACTIVE 버전이 유지되는지 검증합니다. 이후 MCP 검색과 `get_chunk` 출처 추적을 확인하고
테스트 문서를 삭제합니다.

```bash
.venv/bin/python scripts/demo_phase9.py --cleanup
```

주요 확인 항목은 다음과 같습니다.

- 업로드 응답이 즉시 `PENDING`을 반환합니다.
- Worker가 추출 → 청킹 → 임베딩 → 버전 활성화를 수행합니다.
- 새 버전 처리 중 목록 API에는 기존 ACTIVE 버전이 계속 표시됩니다.
- 완료 후 ACTIVE 버전 번호가 원자적으로 변경됩니다.
- MCP 검색 결과가 문서명, 버전, 페이지, 섹션, 청크 ID를 반환합니다.
- 삭제 API가 문서 데이터와 업로드 파일을 제거하고 DELETE 이벤트를 보존합니다.

결과를 파일로 남기려면 다음처럼 실행합니다.

```bash
.venv/bin/python scripts/demo_phase9.py \
  --cleanup \
  --output /tmp/phase9-demo-result.json
```

## 3. 실제 AI 클라이언트 시연

`docs/examples/claude_desktop_config.example.json`을 AI 클라이언트 설정에 맞게 복사하고
DB 비밀번호는 시크릿 관리 방식으로 주입합니다. 채팅에서는 다음 질문을 사용합니다.

> 보안 정책 V1과 V2를 비교해서 비밀번호, 다중 인증, 사고 보고 시간이 어떻게
> 강화되었는지 근거와 함께 표로 정리해 주세요.

AI가 다음 순서로 행동하는 모습을 보여줍니다.

1. `list_documents`로 검색 가능한 정책을 확인합니다.
2. `search_documents`로 관련 청크를 찾습니다.
3. `get_chunk`로 근거 문맥을 확인합니다.
4. 문서 제목·버전·페이지·섹션을 포함한 답변을 작성합니다.

MCP 전송 계층만 독립 검증할 때는 ACTIVE 문서 ID를 지정합니다.

```bash
.venv/bin/python scripts/mcp_smoke_test.py <document_id> \
  --query "데이터베이스 장애 복구 절차는 무엇입니까?"
```

## 4. 검색 품질과 성능

데모 문서를 보존하여 ACTIVE 상태로 둔 경우 다음 평가를 수행할 수 있습니다.

```bash
.venv/bin/python scripts/evaluate_search.py \
  tests/fixtures/search_quality.example.jsonl \
  --top-k 5 --min-recall 0.8 --min-mrr 0.7

.venv/bin/python scripts/benchmark_search.py \
  tests/fixtures/search_quality.example.jsonl \
  --requests 100 --concurrency 10

.venv/bin/python scripts/explain_search.py \
  "비밀번호와 다중 인증 정책"
```

소규모 데이터에서는 PostgreSQL이 순차 스캔을 선택하는 것이 정상일 수 있습니다. HNSW 사용
여부는 충분한 청크를 적재한 환경에서 `--require-hnsw` 옵션으로 판정합니다.

| 지표 | 목표 | 실측 결과 |
|---|---:|---:|
| Recall@5 | ≥ 0.80 | 실행 후 기록 |
| MRR | ≥ 0.70 | 실행 후 기록 |
| 검색 p95 | ≤ 500ms | 실행 후 기록 |
| MCP 성공률 | ≥ 99.5% | 실행 후 기록 |
| 버전 전환 불일치 | 0건 | 실행 후 기록 |

## 5. 개발자 A와의 장애 복구 릴레이

Worker 강제 종료와 Patroni 장애 시연은 개발자 A가 주도합니다. 장애를 복구한 직후 개발자 B는
다음을 확인합니다.

1. `/ready`가 다시 200을 반환합니다.
2. 처리 중이던 버전이 중복 청크 없이 ACTIVE가 됩니다.
3. `scripts/mcp_smoke_test.py`가 네 MCP 도구를 모두 통과합니다.
4. 복구 전후 검색 결과의 출처와 ACTIVE 버전 정합성이 유지됩니다.

단일 노드 환경에서는 Patroni 프로세스를 중지하면 승격할 복제 노드가 없으므로 실제 HA
Failover 시연으로 간주하면 안 됩니다. 다중 노드 구성이 준비된 경우에만 30초 목표를 측정합니다.
