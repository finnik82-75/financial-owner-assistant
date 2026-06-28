import html as _html_mod
from pathlib import Path

from fastapi import APIRouter, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader

from app.config import settings
from app.services.file_service import validate_files, save_uploaded_files
from app.services.parser_service import parse_uploaded_files, save_parsed_result
from app.services.normalizer import (
    load_parsed_json,
    normalize_financial_data,
    save_normalized_result,
    PNL_LABELS,
    CASHFLOW_LABELS,
    CASHFLOW_DETAIL_LABELS,
)
from app.services.knowledge_base_service import count_aliases

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=True,
    cache_size=0,
)


def _fmt_num(value) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.0f}".replace(",", " ")  # narrow no-break space
    except (TypeError, ValueError):
        return str(value)


_env.filters["format_number"] = _fmt_num

templates = Jinja2Templates(env=_env)


def _sorted_cashflow_details(cashflow: dict) -> list[tuple[str, str, float]]:
    """Return (label, key, value) tuples sorted by |value| desc, capped at 12."""
    details = cashflow.get("cashflow_details") or {}
    items: list[tuple[str, str, float]] = []
    for key, label in CASHFLOW_DETAIL_LABELS.items():
        val = details.get(key)
        if val is not None:
            items.append((label, key, val))
    items.sort(key=lambda x: abs(x[2]), reverse=True)
    return items[:12]


def _build_monthly_cf_rows(normalized: dict) -> list[tuple[str, str, dict]]:
    """Return [(month_key, display_name, data_dict), ...] for the monthly CF table."""
    monthly = normalized.get("cashflow", {}).get("monthly") or {}
    if not monthly:
        return []
    period   = normalized.get("period") or {}
    months   = period.get("months") or []
    names    = period.get("month_names") or []
    name_map = dict(zip(months, names)) if months else {}
    rows = []
    for month_key in sorted(monthly.keys()):
        raw_name = name_map.get(month_key, month_key)
        rows.append((month_key, raw_name.capitalize(), monthly[month_key]))
    return rows


_PROJECT_ORDER = [
    "Европа+",
    "Авторадио",
    "Ретро FM",
    'Сайт "Забmedia.ru"',
    "ЗАБ ТВ 24",
    "Наружная реклама",
]


def _build_monthly_pnl_rows(normalized: dict) -> list[tuple[str, str, dict]]:
    """Return [(month_key, display_name, data_dict), ...] for the monthly PnL table."""
    monthly = normalized.get("pnl", {}).get("monthly") or {}
    if not monthly:
        return []
    period   = normalized.get("period") or {}
    months   = period.get("months") or []
    names    = period.get("month_names") or []
    name_map = dict(zip(months, names)) if months else {}
    rows = []
    for month_key in sorted(monthly.keys()):
        raw_name = name_map.get(month_key, month_key)
        rows.append((month_key, raw_name.capitalize(), monthly[month_key]))
    return rows


def _render_report_markdown(text: str) -> str:
    """Convert owner report markdown to HTML for safe display."""
    if not text:
        return ""
    try:
        import markdown as _md
        return _md.markdown(text, extensions=["tables", "nl2br"])
    except Exception:
        return f"<pre>{_html_mod.escape(text)}</pre>"


def _build_project_pnl_rows(normalized: dict) -> list[tuple[str, dict]]:
    """Return [(project_name, data_dict), ...] in canonical project order."""
    projects_data = normalized.get("pnl", {}).get("projects") or {}
    if not projects_data:
        return []
    detected = normalized.get("projects_detected") or []
    order = [p for p in _PROJECT_ORDER if p in projects_data]
    remaining = [p for p in detected if p not in order and p in projects_data]
    extras = [p for p in projects_data if p not in order and p not in remaining]
    return [(p, projects_data[p]) for p in order + remaining + extras]


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request):
    return templates.TemplateResponse(request, "upload.html", {"errors": []})


@router.post("/upload", response_class=HTMLResponse)
async def upload_post(
    request: Request,
    files: list[UploadFile] = File(...),
):
    # Guard: browser may submit an empty file input
    if not files or (len(files) == 1 and not files[0].filename):
        return templates.TemplateResponse(
            request, "upload.html",
            {"errors": ["Файл не выбран. Выберите хотя бы один файл."]},
            status_code=422,
        )

    errors = await validate_files(files)
    if errors:
        return templates.TemplateResponse(
            request, "upload.html", {"errors": errors}, status_code=422
        )

    saved_files = await save_uploaded_files(files)

    files_info = [
        {
            "saved_path":        f["saved_path"],
            "original_filename": f["original_filename"],
            "extension":         f["extension"],
            "size_mb":           f["size_mb"],
        }
        for f in saved_files
    ]

    try:
        parsed_result = parse_uploaded_files(files_info)
        save_parsed_result(parsed_result)
        normalized = normalize_financial_data(parsed_result)
        save_normalized_result(normalized)
    except Exception as exc:
        return templates.TemplateResponse(
            request, "upload.html",
            {"errors": [f"Ошибка обработки файла: {exc}"]},
            status_code=500,
        )

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "result":           normalized,
            "saved_files":      saved_files,
            "pnl_labels":       PNL_LABELS,
            "cf_labels":        CASHFLOW_LABELS,
            "sorted_cf_details":  _sorted_cashflow_details(normalized.get("cashflow", {})),
            "monthly_cf_rows":      _build_monthly_cf_rows(normalized),
            "monthly_pnl_rows":    _build_monthly_pnl_rows(normalized),
            "project_pnl_rows":    _build_project_pnl_rows(normalized),
            "quality":             normalized.get("quality") or {},
            "kpi":                 normalized.get("kpi") or {},
            "owner_report_html":   _render_report_markdown(normalized.get("owner_report", "")),
            "debug_mode":          settings.debug_mode,
        },
    )


@router.post("/extract", response_class=HTMLResponse)
async def extract_post(request: Request):
    form = await request.form()

    saved_paths    = form.getlist("saved_paths")
    original_names = form.getlist("original_names")
    extensions     = form.getlist("extensions")
    size_mbs       = form.getlist("size_mbs")

    if not saved_paths:
        return templates.TemplateResponse(
            request,
            "upload.html",
            {"errors": ["Нет файлов для извлечения. Загрузите файлы заново."]},
            status_code=400,
        )

    upload_root = str(settings.upload_dir.resolve())
    files_info = []
    for path, name, ext, size in zip(saved_paths, original_names, extensions, size_mbs):
        try:
            resolved = str(Path(path).resolve())
        except Exception:
            resolved = ""
        if not resolved.startswith(upload_root):
            return templates.TemplateResponse(
                request,
                "upload.html",
                {"errors": [f"Недопустимый путь: {path}"]},
                status_code=400,
            )
        files_info.append(
            {"saved_path": path, "original_filename": name,
             "extension": ext, "size_mb": float(size)}
        )

    parsed_result = parse_uploaded_files(files_info)
    save_parsed_result(parsed_result)
    return templates.TemplateResponse(request, "parsed.html", {"result": parsed_result})


@router.post("/normalize", response_class=HTMLResponse)
async def normalize_post(request: Request):
    form = await request.form()
    parsed_file_path = form.get("parsed_file_path", "")

    if not parsed_file_path:
        return templates.TemplateResponse(
            request, "upload.html",
            {"errors": ["Путь к parsed JSON не передан. Повторите извлечение."]},
            status_code=400,
        )

    parsed_root = str(settings.parsed_dir.resolve())
    try:
        resolved = str(Path(parsed_file_path).resolve())
    except Exception:
        resolved = ""

    if not resolved.startswith(parsed_root):
        return templates.TemplateResponse(
            request, "upload.html",
            {"errors": [f"Недопустимый путь: {parsed_file_path}"]},
            status_code=400,
        )

    if not Path(parsed_file_path).exists():
        return templates.TemplateResponse(
            request, "upload.html",
            {"errors": [f"Файл не найден: {parsed_file_path}"]},
            status_code=404,
        )

    try:
        parsed_result = load_parsed_json(parsed_file_path)
    except Exception as exc:
        return templates.TemplateResponse(
            request, "upload.html",
            {"errors": [f"Ошибка чтения JSON: {exc}"]},
            status_code=500,
        )

    normalized = normalize_financial_data(parsed_result)
    save_normalized_result(normalized)

    return templates.TemplateResponse(
        request,
        "normalized.html",
        {
            "result":     normalized,
            "pnl_labels": PNL_LABELS,
            "cf_labels":  CASHFLOW_LABELS,
        },
    )


@router.get("/kb", response_class=HTMLResponse)
async def kb_status_page(request: Request):
    kb     = getattr(request.app.state, "knowledge_base", {})
    status = getattr(request.app.state, "kb_status", {"status": "unknown", "errors": [], "warnings": []})
    manifest = kb.get("manifest", {})
    mapping  = kb.get("mapping", {})

    alias_counts = {
        "БДР (pnl)":               count_aliases(mapping.get("pnl", {})),
        "ДДС (cashflow)":          count_aliases(mapping.get("cashflow", {})),
        "Детализация (cashflow_details)": count_aliases(mapping.get("cashflow_details", {})),
    }

    return templates.TemplateResponse(
        request,
        "kb_status.html",
        {
            "kb_version":    manifest.get("version", "—"),
            "kb_name":       manifest.get("name", "—"),
            "kb_status":     status,
            "required_files": manifest.get("required_files", []),
            "alias_counts":  alias_counts,
        },
    )


@router.get("/report/{report_id}", response_class=HTMLResponse)
async def report_page(request: Request, report_id: str):
    return templates.TemplateResponse(
        request, "report.html", {"report_id": report_id, "upload_success": False}
    )


@router.post("/ask-page", response_class=HTMLResponse)
async def ask_page(
    request: Request,
    analysis_id: str = Form(...),
    question: str = Form(""),
):
    from app.services.question_answering_service import answer_question as _answer

    question = question.strip()
    if not question:
        return templates.TemplateResponse(
            request,
            "answer.html",
            {
                "question":     "",
                "answer_html":  "",
                "error":        "Введите вопрос.",
                "analysis_id":  analysis_id,
                "model":        "",
                "debug_mode":   settings.debug_mode,
            },
        )

    result = _answer(question, analysis_id)
    answer_html = _render_report_markdown(result.get("answer_markdown", ""))

    return templates.TemplateResponse(
        request,
        "answer.html",
        {
            "question":     question,
            "answer_html":  answer_html,
            "error":        result.get("error") or "",
            "analysis_id":  analysis_id,
            "model":        result.get("model", ""),
            "debug_mode":   settings.debug_mode,
        },
    )


@router.post("/ask")
async def ask_api(request: Request):
    from app.services.question_answering_service import answer_question as _answer

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "error", "error": "Ожидается JSON body.", "answer_markdown": ""},
            status_code=400,
        )
    analysis_id = (body.get("analysis_id") or "").strip()
    question    = (body.get("question") or "").strip()
    history     = body.get("history") or []
    if not isinstance(history, list):
        history = []
    if not analysis_id or not question:
        return JSONResponse(
            {"status": "error", "error": "Поля analysis_id и question обязательны.", "answer_markdown": ""},
            status_code=400,
        )
    result = _answer(question, analysis_id, history=history or None)
    result["answer_html"] = _render_report_markdown(result.get("answer_markdown", ""))
    result["metadata"] = {
        "intent":   result.pop("intent", None),
        "scenario": result.pop("scenario", None),
    }
    # query_plan and answer_payload already set to None in non-debug mode by service
    status_code = 200 if result.get("status") == "success" else 500
    return JSONResponse(result, status_code=status_code)
