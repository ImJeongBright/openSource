# Tmax_OpenSQL 3.17.8.7 사용자 및 설치 매뉴얼

본 매뉴얼은 `Tmax_OpenSQL_3.17.8.7_rockylinux9.7_buildtime20260720` 패키지의 전체 파일을 분석하여 작성된 상세 사용 안내서입니다.

---

## 1. 개요 및 패키지 구성

Tmax_OpenSQL은 고가용성(HA)을 지원하는 엔터프라이즈급 PostgreSQL 기반 데이터베이스 패키지입니다. 본 패키지는 데이터베이스 엔진뿐만 아니라 클러스터링, 프록시, 백업 등을 위한 필수 구성 요소를 모두 포함하고 있습니다.

### 1.1 지원 환경
- **운영 체제**: Rocky Linux 9.7

### 1.2 주요 구성 요소 (빌드 버전: 3.17.8.7)
- **PostgreSQL**: 17.8 (핵심 데이터베이스 엔진)
- **Patroni**: 4.0.5 (고가용성 및 클러스터 관리)
- **etcd**: 3.6.5 (분산 설정 저장소, DCS)
- **OpenProxy**: v1.1.3 (데이터베이스 프록시 및 연결 라우팅)
- **Barman**: 3.11.1 (재해 복구 및 백업 관리)

### 1.3 포함된 주요 확장 프로그램 (PG Extensions)
- **GIS 및 벡터**: PostGIS 3.5.4, pgvector 0.8.1, pgvectorscale 0.9.0
- **보안 및 감사**: pgaudit 17.1, credcheck 4.2, opencrypto 1.0.0
- **성능 및 튜닝**: pg_hint_plan 1.7.0, pg_profile 4.11, pg_repack 1.5.2
- **기타 유틸리티**: system_stats 3.2, pg_cron 1.6.7, tibero_fdw 0.6.4 등

---

## 2. 설치 전 준비 사항

> [!IMPORTANT]
> 설치를 시작하기 전에 반드시 아래 사항들을 점검해야 합니다.

1. **포트 방화벽 개방**: 각 컴포넌트가 통신할 수 있도록 다음 포트들이 열려 있어야 합니다.
   - **5432**: PostgreSQL 기본 포트
   - **2379 / 2380**: etcd 클라이언트 및 피어 통신 포트
   - **6432 / 6433**: OpenProxy 포트
   - **8008**: Patroni API 포트
2. **라이선스 파일 준비**: 노드별 라이선스 XML 파일(예: `OpenSQL_Trial_...xml`)이 패키지 내 `opensql-installer/licenses/` 폴더에 위치해야 합니다.
3. **권한 확인**: 설치 과정에서는 기본적으로 `sudo` 권한이 필요합니다.
   - 단, 운영 계정에 `sudo` 부여가 불가능한 환경이라면 `config/common.env` 파일에서 `ENABLE_SERVICE=false` 및 `GRANT_OPENSQL_SUDO=false`로 설정한 후, 인프라 담당자가 Root 작업을 별도로 수행해야 합니다.

### 클러스터 모드 선택
설치 시 다음 세 가지 모드 중 하나를 선택할 수 있습니다.
- `single`: 단일 서버 운영 (1대)
- `2node-witness`: 데이터 서버 2대 + Witness 서버 1대 (투표용)
- `3node`: 데이터 서버 3대 (완전한 분산 클러스터)

---

## 3. 환경 설정 가이드

설치 스크립트를 실행하기 전, `opensql-installer/config/` 디렉터리에 있는 환경 설정 파일을 운영 환경에 맞게 수정해야 합니다.

### 3.1 공통 설정 (`config/common.env`)
로컬 및 원격 설치 모두에 적용되는 핵심 설정 파일입니다.

- **노드 IP 및 이름**: 
  - `NODE1_IP`, `NODE2_IP`, `NODE3_IP`: 각 노드의 IP 주소를 입력합니다.
  - `NODE_NAME`: 현재 설정하는 노드의 이름 (예: `node1`)
- **디렉터리 경로**:
  - `OPENSQL_HOME`: OpenSQL 관련 파일이 설치될 홈 경로 (예: `/home/rocky/opensql_home`)
  - `PG_HOME`: PostgreSQL 바이너리 및 라이브러리 경로 (기본적으로 `OPENSQL_HOME/pgsql` 사용)
  - `PG_DATA_DIR`: 실제 데이터가 저장되는 경로 (예: `/home/rocky/opensql_home/data/pgsql`)
- **라이선스**: `LICENSE_NAME` 에 적용할 라이선스 파일명을 기입합니다.
- **기타 옵션**: 
  - `INSTALL_OPENPROXY=true/false`: OpenProxy 설치 여부 결정
  - `AUTO_INSTALL_PREREQS=true/false`: 필수 패키지 자동 설치 여부

### 3.2 원격 설치 전용 설정 (`config/remote.env`)
원격 설치(SSH) 방식을 사용할 때 추가로 작성해야 하는 파일입니다.

- **SSH 접속 정보**: `NODE1_SSH_USER`, `NODE1_SSH_PORT` 등
- **노드별 개별 설정**: 경로(`OPENSQL_HOME`), 라이선스명, 패스워드 등을 각 노드별로 다르게 지정해야 할 경우 여기에 입력합니다.

### 3.3 상세 컴포넌트 설정 (선택 사항)
- `config/patroni.config.env`: Patroni API 포트(8008), 슈퍼유저 비밀번호, 복제 계정 설정 등
- `config/etcd.config.env`: etcd 클러스터 토큰, 디렉터리 이름 등
- `config/openproxy.config.env`: OpenProxy 관련 세부 설정

---

## 4. 설치 실행 방법

설치 방식은 대상 서버에서 직접 스크립트를 실행하는 **로컬 설치**와, 한 서버에서 다른 서버들로 배포하는 **원격 설치** 2가지가 있습니다. 
명령어는 `opensql-installer` 디렉터리 안에서 실행합니다.

### 방법 A: 로컬 설치 (현재 서버 1대씩 개별 설치)

각 서버마다 직접 접속하여 아래 과정을 반복합니다.

1. `config/common.env` 파일 설정값을 현재 노드에 맞게 수정 (`NODE_NAME`, `LICENSE_NAME` 등).
2. 설치 스크립트 실행 (예: 3node 모드)
   ```bash
   cd opensql-installer
   python3 opensql_local_installer.py --mode 3node
   ```
   *스크립트는 현재 서버 IP를 `common.env`의 IP 리스트와 대조하여 자동으로 자신이 몇 번째 노드인지 인식합니다.*

### 방법 B: 원격 설치 (중앙에서 전체 노드 설치)

설치를 주도할 1대의 제어 서버에서 수행합니다.

1. `config/common.env`와 `config/remote.env` 파일을 모두 작성합니다.
2. 아래 명령어로 원격 설치를 시작합니다.
   ```bash
   cd opensql-installer
   python3 opensql_remote_installer.py --mode 3node
   ```
   *만약 SSH 접속을 위해 비밀번호가 필요하다면 `--password '비밀번호'` 옵션을 추가합니다.*

---

## 5. 설치 완료 후 확인 및 사용법

설치가 완료되면 서비스들이 백그라운드(또는 systemd)에서 정상적으로 기동됩니다.

> [!TIP]
> 설치 진행 과정과 결과는 `opensql-installer/logs/` 경로에 생성되는 로그 파일에서 상세히 확인할 수 있습니다.

### 접속 정보 및 사용 포트
데이터베이스를 사용하기 위해 다음 포트들로 접속합니다:
- **일반 데이터베이스 접속 (PostgreSQL)**: `5432` 포트
  - 명령어 예시: `psql -h <노드IP> -p 5432 -U postgres`
- **고가용성 프록시 접속 (OpenProxy)**: `6432` 포트
  - OpenProxy를 통하면 Master 노드 장애 시 자동으로 새로운 Master로 연결이 라우팅됩니다.
- **클러스터 상태 확인 (Patroni)**: `8008` 포트
  - 웹 브라우저나 curl로 `http://<노드IP>:8008/cluster` 에 접속하여 실시간 HA 클러스터 상태를 확인할 수 있습니다.

---

## 6. 고급 기능 (OWLDB 환경 연동)

본 패키지에는 일반적인 온프레미스 환경 외에, **OWLDB** 플랫폼과 연동하기 위한 특수 기능도 내장되어 있습니다. (일반 사용자는 사용하지 않아도 무방합니다.)

### 6.1 OWLDB 설치 연동 (`--owldb`)
설치 상태를 OWLDB 대시보드 API로 실시간 전송하려면 로컬 설치 시 다음 옵션을 함께 부여합니다.
```bash
python3 opensql_local_installer.py --mode 3node --owldb \
  --owldb-ip <IP> --owldb-port <PORT> --owldb-auth-token <TOKEN> \
  --owldb-history-id <ID> --owldb-instance-id <INST_ID>
```

### 6.2 OpenScaler를 통한 동적 스케일링
`opensql-installer/openscaler/` 디렉터리에는 운영 중인 클러스터에 노드를 동적으로 추가(Scale-out)하거나 제거(Scale-in)할 수 있는 도구가 포함되어 있습니다. 이는 주로 가상화/클라우드 환경에서 OWLDB가 노드를 프로비저닝한 뒤 사용합니다. 자세한 사항은 `openscaler/README.md`를 참조하세요.

---

## 자주 발생하는 문제 해결 (Troubleshooting)

- **설치 중 오류 발생 시**: `common.env`에 기입된 현재 서버 IP(`NODE1_IP` 등)가 서버의 실제 네트워크 인터페이스(NIC) IP와 일치하는지 확인하세요. 
- **라이선스 오류**: 지정한 `LICENSE_NAME`이 `licenses/` 폴더 내에 정확히 위치하고, 파일명이 완벽하게 일치하는지 점검하세요.
- **포트 충돌**: 설치를 시작하기 전 기존에 5432, 2379 등 포트를 사용하고 있는 데몬(예: 구버전 postgres 등)이 동작 중인지 `netstat -tlpn` 등으로 확인하세요.
