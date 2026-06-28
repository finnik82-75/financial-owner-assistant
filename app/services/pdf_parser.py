from pathlib import Path

import pdfplumber


def parse_pdf_file(file_path: str) -> dict:
    result = {
        "file_path": file_path,
        "file_type": "pdf",
        "status": "success",
        "pages_count": 0,
        "pages": [],
        "tables_count": 0,
        "warnings": [],
    }

    try:
        with pdfplumber.open(file_path) as pdf:
            result["pages_count"] = len(pdf.pages)
            total_tables = 0

            for i, page in enumerate(pdf.pages):
                page_num = i + 1

                # Text
                raw_text = page.extract_text()
                text = raw_text.strip() if raw_text else ""
                if not text:
                    result["warnings"].append(
                        f"На странице {page_num} не найден текст"
                    )

                # Tables (list of list of list of str|None)
                tables = page.extract_tables() or []
                total_tables += len(tables)

                result["pages"].append(
                    {
                        "page_number": page_num,
                        "text": text,
                        "tables": tables,
                    }
                )

            result["tables_count"] = total_tables
            if total_tables == 0:
                result["warnings"].append("В PDF не найдены таблицы")

    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"Не удалось открыть PDF-файл: {exc}"
        result["pages"] = []

    return result
