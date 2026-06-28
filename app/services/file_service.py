import re
import uuid
from pathlib import Path
from datetime import datetime
from fastapi import UploadFile

from app.config import settings


def ensure_upload_dir() -> None:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)


def get_file_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


async def validate_files(files: list[UploadFile]) -> list[str]:
    errors = []

    real_files = [f for f in files if f.filename]
    if not real_files:
        return ["Не выбрано ни одного файла"]

    if len(real_files) > settings.max_files:
        errors.append(
            f"Максимум {settings.max_files} файлов за раз. Вы выбрали: {len(real_files)}"
        )
        return errors

    total_size = 0
    for file in real_files:
        ext = get_file_extension(file.filename)
        if ext not in settings.allowed_extensions:
            errors.append(
                f"«{file.filename}»: недопустимый формат {ext!r}. "
                f"Разрешены: .xlsx, .xls, .pdf"
            )
            continue

        await file.seek(0)
        content = await file.read()
        size_bytes = len(content)
        await file.seek(0)

        size_mb = size_bytes / (1024 * 1024)
        if size_mb > settings.max_file_size_mb:
            errors.append(
                f"«{file.filename}»: размер {size_mb:.1f} МБ превышает лимит "
                f"{settings.max_file_size_mb} МБ"
            )

        total_size += size_bytes

    total_mb = total_size / (1024 * 1024)
    if total_mb > settings.max_total_size_mb and not errors:
        errors.append(
            f"Общий размер файлов {total_mb:.1f} МБ превышает лимит "
            f"{settings.max_total_size_mb} МБ"
        )

    return errors


async def save_uploaded_files(files: list[UploadFile]) -> list[dict]:
    ensure_upload_dir()
    result = []
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for file in files:
        if not file.filename:
            continue

        ext = get_file_extension(file.filename)
        stem = re.sub(r"[^\w\-]", "_", Path(file.filename).stem)
        uid = str(uuid.uuid4())[:8]
        saved_filename = f"{ts}_{uid}_{stem}{ext}"
        saved_path = settings.upload_dir / saved_filename

        await file.seek(0)
        content = await file.read()
        saved_path.write_bytes(content)

        result.append(
            {
                "original_filename": file.filename,
                "saved_filename": saved_filename,
                "saved_path": str(saved_path),
                "extension": ext,
                "size_mb": round(len(content) / (1024 * 1024), 2),
            }
        )

    return result
