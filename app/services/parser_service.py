import json
import uuid
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.services.excel_parser import parse_excel_file
from app.services.pdf_parser import parse_pdf_file


def parse_uploaded_file(file_info: dict) -> dict:
    ext = file_info.get("extension", "").lower()
    file_path = file_info.get("saved_path", "")

    if ext in (".xlsx", ".xls"):
        doc = parse_excel_file(file_path)
    elif ext == ".pdf":
        doc = parse_pdf_file(file_path)
    else:
        doc = {
            "file_path": file_path,
            "file_type": "unknown",
            "status": "error",
            "error": f"Неизвестный формат файла: {ext}",
            "warnings": [],
        }

    doc["original_filename"] = file_info.get("original_filename", "")
    return doc


def parse_uploaded_files(files_info: list) -> dict:
    analysis_id = (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + str(uuid.uuid4())[:8]
    )
    documents = []
    global_warnings = []

    for file_info in files_info:
        doc = parse_uploaded_file(file_info)
        documents.append(doc)
        if doc["status"] == "error":
            global_warnings.append(
                f"Ошибка при обработке «{file_info.get('original_filename', '')}»: "
                f"{doc.get('error', '')}"
            )

    if not documents:
        overall_status = "error"
    elif all(d["status"] == "error" for d in documents):
        overall_status = "error"
    elif any(d["status"] == "error" for d in documents):
        overall_status = "partial"
    else:
        overall_status = "success"

    parsed_file_path = str(
        settings.parsed_dir / f"{analysis_id}_parsed.json"
    )

    return {
        "analysis_id": analysis_id,
        "status": overall_status,
        "files_count": len(documents),
        "documents": documents,
        "global_warnings": global_warnings,
        "parsed_file_path": parsed_file_path,
    }


def save_parsed_result(parsed_result: dict) -> str:
    settings.parsed_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(parsed_result["parsed_file_path"])
    out_path.write_text(
        json.dumps(parsed_result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return str(out_path)
