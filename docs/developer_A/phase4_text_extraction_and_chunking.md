# Phase 4: 텍스트 추출 및 청킹(Chunking) 구현 상세

## 1. 개요
본 단계에서는 파일로부터 텍스트를 추출하고 이를 의미 단위의 청크(Chunk)로 분할하여 파이프라인의 시작점을 구축합니다. 지원 포맷은 PDF, TXT, Markdown입니다. 본 단계에서 생성된 청크는 이후 벡터 임베딩을 거쳐 검색에 활용되므로, 추출의 정확성과 청킹의 무결성이 가장 중요합니다.

구현 기준은 현재 공유 모델인 `src/models.py`를 따릅니다. 추출 결과는
`TextBlock(text, page, section)`, 청크 결과는 `ChunkData(index, content,
page_number, section_title, char_start, char_end)` 형태를 사용합니다.

## 2. 세부 구현 목표

### 2.1. 텍스트 추출기 개발 (`src/pipeline/extractor.py`)

#### PDF 추출 로직
- **라이브러리**: `PyMuPDF (fitz)` 사용
- **처리 단위**: 페이지 단위 텍스트 추출
- **예외 처리 및 방어 로직**: 
  - 이미지로만 구성된 스캔본 페이지는 기능 요구사항에 따라 건너뛰고, 로그에 페이지 번호와 OCR 미지원 사실을 기록합니다.
  - OCR을 수행한 것처럼 빈 문자열을 정상 텍스트로 저장하지 않습니다. OCR 지원은 별도 범위로 둡니다.
- **메타데이터 유지**: 각 추출된 텍스트 블록에 원본 페이지 번호를 정확히 매핑하여 검색 시 출처(Traceability)를 제공합니다.

#### Markdown 추출 로직
- **라이브러리**: `mistune` 등 마크다운 파서 활용
- **처리 단위**: 헤더(H1, H2, H3) 구조 기반의 섹션 단위 텍스트 추출
- **컨텍스트 유지**: 각 문단이 어느 섹션에 속하는지 추적하여, 상위 헤더의 정보를 텍스트 메타데이터로 남깁니다. (예: `section_title`)

#### 출력 포맷 공통화
- PDF, TXT, Markdown 등 서로 다른 소스에서 추출된 결과를 단일 포맷으로 통일합니다.
- TXT는 `TextBlock(text=..., page=None, section=None)`으로 반환할 수 있습니다.
- 이미 정의된 `TextBlock` Pydantic 모델을 재정의하지 않고 다음 필드를 사용합니다:
  - `text`: 순수 텍스트
  - `page`: 페이지 번호 (PDF 등 해당 시)
  - `section`: 섹션 또는 헤더 제목 (Markdown 등 해당 시)

### 2.2. 청킹 알고리즘 적용 (`src/pipeline/chunker.py`)

#### 슬라이딩 윈도우 기반 청킹
- **단위**: `chunk_size`와 `overlap`은 문자 수가 아니라 임베딩 모델 토큰 수로 고정합니다. MVP에서는 `tiktoken`으로 `text-embedding-3-small` 인코딩을 사용하고, 문자 단위로 조용히 대체하지 않습니다.
- **기본 파라미터**: `chunk_size=512`, `overlap=50`. 실제 값은 Worker가 해당 `document_versions` 레코드에서 읽어 `chunk_text(..., chunk_size, overlap)`에 명시적으로 전달합니다.
- **스플릿 기준**: 
  - 토큰 수 기준을 넘지 않는 범위에서 문단·줄바꿈·문장 경계를 우선 사용합니다. 경계가 없는 긴 문장은 토큰 단위로 자릅니다.
  - 모든 청크는 `len(tokens) <= chunk_size`를 만족해야 하며, `0 <= overlap < chunk_size`를 검증합니다.
  - 원문 기준 `char_start`/`char_end`를 계산할 수 없는 포맷은 임의의 위치를 기록하지 말고 `None`으로 둡니다.

#### 청크 무결성 검증
- **유닛 테스트 (`pytest`)**: 
  - 긴 텍스트 입력 시 설정한 `chunk_size` 및 `overlap`에 맞게 정확히 분할되는지 검증하는 테스트 케이스를 작성합니다.
  - 분할된 각 청크가 부모 `TextBlock`의 메타데이터(섹션명, 페이지 번호)를 손실 없이 그대로 상속받는지 확인합니다.
  - 예외 케이스(극단적으로 긴 단어, 특수 기호가 많은 텍스트)에 대한 테스트를 추가합니다.

## 3. 연관 시스템 및 인터페이스
- Worker는 업로드 API가 저장한 원본 파일을 `UPLOAD_DIR/<version_id>.<확장자>`처럼 버전 ID로 결정되는 경로에서 읽습니다. 파일 경로를 사용자 입력으로 직접 조합하지 않습니다.
- `TextBlock`과 `ChunkData`는 메모리에서 다음 단계로 전달하고, 청크 INSERT 및 `total_chunks` 갱신은 Worker가 하나의 트랜잭션으로 수행합니다.
- 청킹 모듈 자체는 DB에 접근하지 않습니다. 버전별 `chunk_size`/`chunk_overlap` 조회는 Worker의 책임입니다.
