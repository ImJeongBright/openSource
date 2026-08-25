# Phase 4 구현 완료 보고서

**담당**: Developer A  
**단계**: 텍스트 추출 및 청킹  
**완료일**: 2026-08-24  
**브랜치**: `feat/api`

## 1. 완료 요약

파일에서 텍스트를 추출하고, 임베딩 모델 토큰 기준으로 청크를 생성하는 Phase 4 기본 구현을 완료했습니다.

- PDF, TXT, Markdown 추출 구현
- `TextBlock` 및 `ChunkData` 공유 모델 사용
- `tiktoken` 기반 슬라이딩 윈도우 청킹 구현
- 문장·줄바꿈 경계 우선 분할
- 페이지·섹션 메타데이터 보존
- 청크별 문자 오프셋 및 순번 생성
- 입력 오류와 청킹 파라미터 검증
- Developer A 전용 단위 테스트 15개 작성

## 2. TDD 진행 과정

### RED

먼저 테스트를 작성하고 실행했습니다. 기존 `extractor.py`와 `chunker.py`가 `NotImplementedError` 상태였기 때문에 14개 테스트가 실패했습니다.

이 단계에서 다음 구현 계약을 테스트로 고정했습니다.

- TXT의 UTF-8 보존
- Markdown heading context 보존
- PDF 페이지 번호 매핑
- 텍스트가 없는 PDF 페이지 건너뛰기
- 토큰 최대 길이와 overlap
- 문장 경계 우선 처리
- 문자 오프셋 보존
- 잘못된 파라미터 거부

### GREEN

테스트가 요구하는 최소 동작을 구현했습니다.

- `extractor.py`: 파일 형식별 추출기 구현
- `chunker.py`: 토큰 기반 청킹 및 메타데이터 변환 구현
- PDF fixture의 한글 폰트 문제를 피하기 위해 PDF 페이지 매핑 테스트는 ASCII 텍스트로 검증

### 결과

```text
15 passed in 0.62s
```

## 3. 구현 내용

### 3.1 `src/pipeline/extractor.py`

- `pdf`, `txt`, `markdown`과 `md`, `text` 별칭을 지원합니다.
- 존재하지 않는 파일은 `FileNotFoundError`를 반환합니다.
- 지원하지 않는 형식은 `ValueError`를 반환합니다.
- PDF는 페이지별로 `TextBlock(text, page)`을 생성하며 페이지 번호는 1부터 시작합니다.
- 텍스트가 없는 PDF 페이지는 OCR을 시도하지 않고 건너뛰며 로그를 남깁니다.
- TXT는 UTF-8로 읽고 하나의 `TextBlock`으로 반환합니다.
- Markdown은 Mistune AST를 사용하여 heading 이후 문단에 현재 섹션 제목을 붙입니다.

### 3.2 `src/pipeline/chunker.py`

- `text-embedding-3-small`용 `tiktoken` 인코딩을 사용합니다.
- `chunk_size`와 `overlap`을 함수 인자로 받아 버전별 설정을 주입할 수 있습니다.
- 기본값은 설정 객체의 `CHUNK_SIZE=512`, `CHUNK_OVERLAP=50`입니다.
- 문장부호와 줄바꿈 경계를 우선 선택하되, 청크가 최대 토큰 수를 넘지 않도록 합니다.
- 하나의 `TextBlock` 내부에서 청킹하며 페이지·섹션 경계를 넘어 청크를 합치지 않습니다.
- `ChunkData.index`는 전체 결과에서 0부터 연속적으로 증가합니다.
- `char_start`와 `char_end`는 현재 `TextBlock` 내부 기준 오프셋입니다.
- `chunk_size <= 0`, `overlap < 0`, `overlap >= chunk_size`를 거부합니다.

## 4. 추가된 테스트 파일

```text
tests/conftest.py
tests/pipeline/test_extractor.py
tests/pipeline/test_chunker.py
```

`tests/conftest.py`는 Phase 4 테스트가 DB나 외부 API 없이 실행되도록 테스트용 설정값과 프로젝트 import 경로를 제공합니다.

## 5. 검증 방법

프로젝트 전용 가상환경에서 다음 명령으로 실행했습니다.

```bash
TIKTOKEN_CACHE_DIR=/tmp/opensql-tiktoken-cache \
  .venv/bin/pytest tests/pipeline -q
```

추가로 Python 컴파일 검증과 Markdown fence 검증도 수행해야 합니다.

## 6. 현재 부족한 부분 및 제한사항

Phase 4 범위에서 의도적으로 남겨둔 항목입니다.

1. **OCR 미지원**: 이미지 전용 PDF 페이지는 건너뛰기만 하며 OCR을 수행하지 않습니다.
2. **원문 전역 오프셋 미지원**: `TextBlock`에 원본 파일 전역 위치 정보가 없으므로 오프셋은 블록 기준입니다.
3. **DB 저장 미연결**: 생성한 청크를 `doc_search.chunks`에 저장하고 `total_chunks`를 갱신하는 작업은 Worker 통합 단계에서 수행합니다.
4. **원본 파일 lifecycle 미연결**: 업로드 API의 staging 파일과 Worker 처리 후 삭제 정책은 Phase 5·6 통합 시 연결해야 합니다.
5. **인코딩 파일 초기 다운로드**: `tiktoken`은 첫 실행 시 모델 인코딩 파일을 내려받을 수 있으므로 배포 환경에서 사전 캐시 또는 네트워크 정책을 확인해야 합니다.
6. **복합 Markdown 검증 부족**: 표, 중첩 목록, HTML, 복잡한 inline directive에 대한 별도 fixture는 아직 없습니다.

## 7. 다음 단계

Phase 4 산출물을 기준으로 다음 단계에서 Worker를 연결합니다.

- `change_log` 작업 선점
- 파일 경로로부터 `extract_text()` 호출
- 버전별 `chunk_size`/`chunk_overlap` 주입
- 생성된 `ChunkData` DB 저장
- 실패 시 재시도 및 lease 복구

이 단계부터는 Worker가 사용하는 공유 인터페이스와 원본 파일 경로 계약을 Developer B와 먼저 확인한 뒤 진행합니다. Developer B 구현 파일은 이번 Phase 4 작업에서 수정하지 않았습니다.
