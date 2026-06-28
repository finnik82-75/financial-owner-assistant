import json
from pathlib import Path

import pandas as pd


def parse_excel_file(file_path: str) -> dict:
    result = {
        "file_path": file_path,
        "file_type": "excel",
        "status": "success",
        "sheets": [],
        "warnings": [],
    }

    suffix = Path(file_path).suffix.lower()
    open_kwargs = {"engine": "openpyxl"} if suffix == ".xlsx" else {}

    try:
        xl = pd.ExcelFile(file_path, **open_kwargs)
    except Exception as exc:
        result["status"] = "error"
        result["error"] = f"Не удалось открыть Excel-файл: {exc}"
        return result

    for sheet_name in xl.sheet_names:
        try:
            df = pd.read_excel(xl, sheet_name=sheet_name, header=None)

            # Drop entirely empty rows and columns
            df = df.dropna(how="all").dropna(axis=1, how="all")

            if df.empty:
                result["warnings"].append(f"Лист «{sheet_name}» пустой, пропущен")
                continue

            # Positional column names so the normaliser can apply own headers
            df.columns = [f"col_{i}" for i in range(len(df.columns))]

            # JSON round-trip converts NaN→None, numpy types, Timestamps to Python natives
            records = json.loads(
                df.to_json(orient="records", force_ascii=False, date_format="iso")
            )

            result["sheets"].append(
                {
                    "sheet_name": str(sheet_name),
                    "rows_count": len(df),
                    "columns_count": len(df.columns),
                    "columns": list(df.columns),
                    "records": records,
                }
            )

        except Exception as exc:
            result["warnings"].append(
                f"Ошибка при чтении листа «{sheet_name}»: {exc}"
            )

    if not result["sheets"]:
        result["warnings"].append("В Excel-файле не найдено данных ни на одном листе")

    return result
