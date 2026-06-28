import json
from pathlib import Path
from datetime import datetime

from app.config import settings


def build_report(session_id: str, kpis: dict, quality: dict, analysis: str) -> dict:
    """Собирает итоговый отчёт и сохраняет его в data/outputs."""
    report = {
        "session_id": session_id,
        "created_at": datetime.now().isoformat(),
        "kpis": kpis,
        "quality": quality,
        "analysis": analysis,
    }
    out_path = settings.output_dir / f"{session_id}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def load_report(session_id: str) -> dict | None:
    out_path = settings.output_dir / f"{session_id}.json"
    if not out_path.exists():
        return None
    return json.loads(out_path.read_text(encoding="utf-8"))
