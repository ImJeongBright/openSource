from fastapi import FastAPI, UploadFile, File, Form

app = FastAPI(title="OpenSQL Doc Search API")

@app.post("/api/documents")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    category: str = Form(None)
):
    """
    [개발자 B 구현] 파일 업로드 및 DB 트랜잭션 시작
    """
    raise NotImplementedError("Phase 5에서 개발자 B가 구현할 예정입니다.")

@app.get("/api/documents/{document_id}/status")
async def get_document_status(document_id: str):
    """
    [개발자 B 구현] 문서 처리 진행 상태 조회
    """
    raise NotImplementedError("Phase 5/7에서 개발자 B가 구현할 예정입니다.")
