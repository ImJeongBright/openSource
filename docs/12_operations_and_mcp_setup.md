# API·Worker 운영 및 MCP 클라이언트 연결

## 1. 운영 서비스

저장소의 `opensql-api.service`와 `opensql-worker.service`는 Rocky Linux 서버의
`/etc/systemd/system/`에 설치하는 템플릿입니다. 두 서비스는 `rocky` OS 계정으로 실행하고,
SELinux가 systemd의 홈 디렉터리 비밀 파일 접근을 차단하지 않도록 `.env`를 권한 600으로
`/etc/opensql-doc-search/app.env`에 복사합니다. 업로드 디렉터리 외의 프로젝트 파일은
읽기 전용으로 취급합니다.

```bash
sudo install -m 0644 opensql-api.service /etc/systemd/system/
sudo install -m 0644 opensql-worker.service /etc/systemd/system/
sudo install -d -m 0750 /etc/opensql-doc-search
sudo install -m 0600 .env /etc/opensql-doc-search/app.env
sudo ./scripts/provision_runtime_roles.sh
sudo systemctl daemon-reload
sudo systemctl enable --now opensql-api opensql-worker
```

역할 프로비저닝 스크립트는 `opensql_api`, `opensql_worker`, `mcp_app_user`의 비밀번호를
각각 무작위로 생성하고 최소 권한을 부여합니다. 비밀번호는 화면에 출력하지 않으며
`/etc/opensql-doc-search/{api,worker,mcp}.env`에 `root:rocky`, 권한 640으로 저장합니다.
기존 `app_user`는 즉시 제거하지 않으므로 전환 검증 중 롤백할 수 있습니다. 스크립트를 다시
실행하면 세 계정의 비밀번호가 회전하므로 API와 Worker를 함께 재시작해야 합니다.

Rocky Linux의 SELinux는 `systemd`가 홈 디렉터리의 가상환경 바이너리를 직접 `exec`하는 것을
차단할 수 있습니다. 제공된 단위 파일은 SELinux 정책을 비활성화하거나 별도 허용 규칙을
추가하지 않고 `/bin/bash`를 고정 진입점으로 사용한 뒤 가상환경 Python으로 전환합니다.

API는 기본적으로 `127.0.0.1:8000`에서만 수신합니다. 외부 공개가 필요하면 TLS를 종료하는
리버스 프록시를 앞에 두고 필요한 경로만 노출합니다. DB, Patroni, etcd, Ollama 포트를
인터넷에 직접 공개하지 않습니다.

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/ready
```

`/health`는 API 프로세스 생존 여부만 확인합니다. `/ready`는 OpenSQL 연결과 설정된
Ollama 모델 설치 여부를 모두 확인하며 하나라도 실패하면 HTTP 503을 반환합니다.

## 2. MCP stdio 연결

MCP 서버는 HTTP 데몬이 아니라 AI 클라이언트가 자식 프로세스로 실행하는 stdio 서버입니다.
예시는 `docs/examples/claude_desktop_config.example.json`에 있습니다. 비밀번호는 예시 파일에
기록하지 않고 실제 클라이언트 설정 또는 시크릿 관리 도구에서 주입합니다.
서버에서 직접 MCP를 실행할 때는 보호된 `/etc/opensql-doc-search/mcp.env`의 값을 실행 환경에
주입합니다. MCP 계정에는 조회 권한만 있고 문서·버전·청크·벡터를 수정할 수 없습니다.

서버가 공개하는 도구는 다음 네 개뿐입니다.

- `search_documents`
- `get_document`
- `list_documents`
- `get_chunk`

구성 확인은 다음처럼 수행합니다.

```bash
.venv/bin/python mcp/server.py --check
```

## 3. 검색 품질과 성능

```bash
make evaluate DATASET=tests/fixtures/search_quality.example.jsonl
make benchmark DATASET=tests/fixtures/search_quality.example.jsonl
make explain-search QUERY="데이터베이스 장애 복구 절차"
```

평가 데이터셋의 문서가 먼저 ACTIVE 상태로 적재되어 있어야 합니다. `evaluate_search.py`는
Recall@K, MRR, p50/p95 지연시간을 출력하고 `benchmark_search.py`는 동시 검색 성공률과
처리량을 측정합니다. 실행계획 도구는 쿼리 또는 원시 벡터를 출력하지 않습니다.

## 4. HNSW 유지보수

`sql/maintenance/rebuild_hnsw.sql`은 기존 인덱스를 유지한 상태에서 대체 인덱스를 먼저
작성한 뒤 짧은 트랜잭션으로 이름을 교체합니다. 재구축 중에는 인덱스 저장공간이 일시적으로
두 배 필요합니다.

```bash
psql -v hnsw_m=24 -v hnsw_ef_construction=128 \
  -f sql/maintenance/rebuild_hnsw.sql
```

파라미터를 변경하기 전에는 동일한 평가 데이터와 부하 조건에서 Recall@K와 p95를 비교합니다.
