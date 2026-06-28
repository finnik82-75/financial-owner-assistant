from fastapi import APIRouter, UploadFile, File, HTTPException

from app.services.file_service import validate_files, save_uploaded_files

router = APIRouter(tags=["api"])


@router.post("/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    errors = await validate_files(files)
    if errors:
        raise HTTPException(400, detail=errors)

    saved = await save_uploaded_files(files)
    return {"files": saved, "status": "uploaded"}


@router.get("/health")
async def health():
    return {"status": "ok"}
