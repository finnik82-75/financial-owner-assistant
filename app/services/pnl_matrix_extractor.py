"""
Extract monthly and project-level P&L values from a multi-column БДР matrix.

Two extraction paths:
  - Structured (table rows): each cell is a separate string → parse with _parse_num.
    Used by extract_pnl_matrix_from_rows (primary path for table PDFs).
  - Text-line: numbers are embedded in a single string → parse with _extract_nums.
    Used by extract_pnl_matrix (fallback for plain-text / Excel inputs).

Expected table layout for n_months months and n_projects projects:
  label | [project values × n_projects] | month_total | (repeated per month) |
         [project totals × n_projects] | grand_total
  Total value cells per data row = (n_months + 1) × (n_projects + 1)
"""
import re

# ─── Internal regex — same as normalizer._EMBEDDED_NUMBER_RE ─────────────────
_EMBEDDED_NUMBER_RE = re.compile(
    r"-?\d{1,3}(?:[ \xa0]\d{3})+(?:[,.]\d+)?(?!\d)"
    r"|-?\d+(?:[,.]\d+)?"
)

_OCR_FIXES: list[tuple[str, str]] = [
    ("с у четом", "с учетом"),
    ("у четом",   "учетом"),
    ("у чета",    "учета"),
    ("осн. деятельности", "основной деятельности"),
]

# Labels that look like tracked metrics but must be excluded from extraction.
_EXCLUSIONS: frozenset[str] = frozenset({
    "без учета вго",
    "до выплаты дивидендов",
    "до налогообложения",
    "накопительно",
})

# PnL metric patterns — ordered most-specific → least-specific.
_PNL_METRIC_PATTERNS: list[tuple[str, str]] = [
    ("прибыль с учетом прочей деятельности",  "profit_after_other_activity"),
    ("прибыль без учета амортизации",          "ebitda_proxy"),
    ("ebitda",                                 "ebitda_proxy"),
    ("прибыль от основной деятельности",       "operating_profit"),
    ("прибыль от продаж",                      "operating_profit"),
    ("операционная прибыль",                   "operating_profit"),
    ("валовый доход",                          "gross_profit"),
    ("валовая прибыль",                        "gross_profit"),
    ("валовой доход",                          "gross_profit"),
    ("выручка от реализации",                  "revenue"),
    ("выручка от продаж",                      "revenue"),
    ("выручка",                                "revenue"),
    ("доходы от рекламы",                      "revenue"),   # only if revenue not yet seen
    ("себестоимость",                          "cogs"),
    ("коммерческие расходы",                   "operating_expenses"),
    ("административные расходы",               "operating_expenses"),
    ("управленческие расходы",                 "operating_expenses"),
    ("операционные расходы",                   "operating_expenses"),
    ("расходы",                                "operating_expenses"),
]

# Separator for the text-line path (must NOT be a decimal/digit character)
_LINE_SEP = "|"

# Split label from numbers in a text line
_NUM_START_RE = re.compile(r"\s+[-–−]?\d")


# ─── Private helpers ──────────────────────────────────────────────────────────

def _normalize_label(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[«»\"'(){}\[\]/:;,!?*]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for bad, good in _OCR_FIXES:
        s = s.replace(bad, good)
    return s


def _parse_num(raw: str) -> float | None:
    """Parse a Russian/European formatted number string."""
    s = str(raw).strip()
    if not s or s in ("-", "—", "–", "н/д", "нет", "null", "none"):
        return None
    is_neg = False
    if s.startswith("(") and s.endswith(")"):
        s, is_neg = s[1:-1].strip(), True
    elif s.startswith("-"):
        s, is_neg = s[1:].strip(), True
    if "%" in s:
        return None
    s = re.sub(r"(руб\.?|тыс\.?|млн\.?|млрд\.?|[₽$€£])", "", s, flags=re.IGNORECASE).strip()
    s = s.replace(" ", "").replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    dots, commas = s.count("."), s.count(",")
    if dots > 1 and commas == 0:
        s = s.replace(".", "")
    elif dots == 1 and commas == 1:
        if s.index(".") < s.index(","):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif commas == 1 and dots == 0:
        s = s.replace(",", ".")
    elif commas > 1 and dots == 0:
        s = s.replace(",", "")
    try:
        val = float(s)
        return -val if is_neg else val
    except ValueError:
        return None


def _extract_nums(text: str) -> list[float]:
    """Extract money-like numbers from a mixed text+number string (text-line path)."""
    text = re.sub(r"^[·•\-–—]+\s*", "", text).strip()
    results: list[float] = []
    for m in _EMBEDDED_NUMBER_RE.finditer(text):
        raw = m.group()
        end = m.end()
        if end < len(text) and text[end] == "%":
            trimmed = re.sub(r"[ \xa0]\d{3}$", "", raw)
            if trimmed and trimmed != raw:
                n = _parse_num(trimmed)
                if n is not None:
                    results.append(n)
            continue
        n = _parse_num(raw)
        if n is not None:
            results.append(n)
    return results


def _match_pnl_metric_label(label: str) -> str | None:
    """Match a row label string to a canonical PnL field name."""
    norm   = _normalize_label(label)
    padded = f" {norm} "
    for excl in _EXCLUSIONS:
        if excl in padded:
            return None
    for pattern, field in _PNL_METRIC_PATTERNS:
        if pattern in padded:
            return field
    return None


def _match_pnl_metric(line: str) -> str | None:
    """Extract label from a text line, then match against metric patterns."""
    if _LINE_SEP in line:
        label = line.split(_LINE_SEP)[0].strip()
    else:
        m = _NUM_START_RE.search(line)
        label = line[:m.start()].strip() if m else line.strip()
    return _match_pnl_metric_label(label)


def _rows_to_lines(rows: list[dict]) -> list[str]:
    """
    Convert structured rows to text lines for the text-line extraction path.
    Uses _LINE_SEP ("|") as separator so the greedy spaced-thousands regex
    does NOT merge adjacent cell values into one giant number.
    """
    lines: list[str] = []
    for row in rows:
        lbl = row["label"].strip()
        if not lbl:
            continue
        parts = [lbl]
        for v in row.get("values", []):
            if v is None:
                parts.append("0")
            else:
                sv = str(v).replace("\n", " ").replace("\r", " ").strip()
                parts.append(sv if sv not in ("", "-", "—", "–") else "0")
        lines.append(_LINE_SEP.join(parts))
    return lines


def _decompose_matrix(
    nums: list[float],
    n_months: int,
    months: list[str],
    n_projects: int,
    projects: list[str],
) -> dict:
    """
    Decompose a flat list of values (length = (n_months+1)×(n_projects+1)) into
    monthly × project dictionaries.  Shared by both extraction paths.
    """
    block_size = n_projects + 1
    project_monthly: dict = {}
    monthly:         dict = {}
    projects_total:  dict = {}

    for b in range(n_months):
        offset    = b * block_size
        month_key = months[b]
        for p_idx, proj in enumerate(projects):
            project_monthly.setdefault(proj, {})[month_key] = nums[offset + p_idx]
        monthly[month_key] = nums[offset + n_projects]

    totals_offset = n_months * block_size
    for p_idx, proj in enumerate(projects):
        projects_total[proj] = nums[totals_offset + p_idx]
    period_total = nums[totals_offset + n_projects]

    return {
        "project_monthly": project_monthly,
        "monthly":         monthly,
        "projects":        projects_total,
        "period_total":    period_total,
    }


# ─── Public API ───────────────────────────────────────────────────────────────

def extract_metric_matrix_from_line(
    line: str,
    metric_key: str,
    period: dict,
    projects: list[str],
) -> dict:
    """
    Decompose one metric text line into monthly × project matrix.

    The line may use _LINE_SEP ("|") as cell separator (produced by _rows_to_lines)
    or may be a plain string with embedded numbers.

    Returns dict with keys "project_monthly", "monthly", "projects", "period_total",
    or empty dict if there are not enough numbers.
    """
    months     = period.get("months") or []
    n_months   = len(months)
    n_projects = len(projects)
    if n_months == 0 or n_projects == 0:
        return {}

    block_size = n_projects + 1
    expected   = (n_months + 1) * block_size

    # Parse numbers — use structured cells if separator present
    if _LINE_SEP in line:
        parts = line.split(_LINE_SEP)[1:]          # skip label at [0]
        nums = [n for p in parts for n in [_parse_num(p)] if n is not None]
    else:
        nums = _extract_nums(line)

    if len(nums) < expected:
        return {}

    return _decompose_matrix(nums[:expected], n_months, months, n_projects, projects)


def extract_pnl_matrix(
    lines: list[str],
    period: dict,
    projects: list[str],
) -> dict:
    """
    Text-line path: iterate lines and extract the monthly × project P&L matrix.
    Lines should be produced by _rows_to_lines (separator-aware) or come from
    a plain-text source where numbers are embedded in each line.

    Returns:
      {
        "monthly":         {month_key: {field: value}},
        "projects":        {project_name: {field: value}},
        "project_monthly": {project_name: {month_key: {field: value}}},
        "period_totals":   {field: grand_total},
        "_warnings":       [str],
      }
    """
    result: dict = {
        "monthly": {}, "projects": {}, "project_monthly": {},
        "period_totals": {}, "_warnings": [],
    }
    months     = period.get("months") or []
    n_months   = len(months)
    n_projects = len(projects)
    if n_months == 0 or n_projects == 0:
        return result

    expected     = (n_months + 1) * (n_projects + 1)
    seen_metrics: set[str] = set()

    for line in lines:
        metric_key = _match_pnl_metric(line)
        if metric_key is None or metric_key in seen_metrics:
            continue

        matrix = extract_metric_matrix_from_line(line, metric_key, period, projects)
        if not matrix:
            # Count actual numbers for the warning message
            if _LINE_SEP in line:
                parts = line.split(_LINE_SEP)[1:]
                n_found = sum(1 for p in parts if _parse_num(p) is not None)
            else:
                n_found = len(_extract_nums(line))
            result["_warnings"].append(
                f"Не удалось разложить строку БДР по проектам и месяцам: {metric_key} "
                f"(найдено {n_found} чисел, ожидалось {expected})"
            )
            continue

        seen_metrics.add(metric_key)
        for month_key, total in matrix["monthly"].items():
            result["monthly"].setdefault(month_key, {})[metric_key] = total
        for proj, total in matrix["projects"].items():
            result["projects"].setdefault(proj, {})[metric_key] = total
        for proj, month_data in matrix["project_monthly"].items():
            pm = result["project_monthly"].setdefault(proj, {})
            for month_key, val in month_data.items():
                pm.setdefault(month_key, {})[metric_key] = val
        if matrix.get("period_total") is not None:
            result["period_totals"][metric_key] = matrix["period_total"]

    return result


def extract_pnl_matrix_from_rows(
    rows: list[dict],
    period: dict,
    projects: list[str],
) -> dict:
    """
    Structured path: work directly with rows from extract_rows_from_document.
    Each cell is parsed individually with _parse_num, avoiding the greedy-regex
    issue that arises when Russian-formatted numbers are space-joined into text.

    Falls back to the text-line path for rows that have no structured values
    (text-mode PDFs where label contains the full line with embedded numbers).
    """
    result: dict = {
        "monthly": {}, "projects": {}, "project_monthly": {},
        "period_totals": {}, "_warnings": [],
    }
    months     = period.get("months") or []
    n_months   = len(months)
    n_projects = len(projects)
    if n_months == 0 or n_projects == 0:
        return result

    block_size   = n_projects + 1
    expected     = (n_months + 1) * block_size
    seen_metrics: set[str] = set()

    for row in rows:
        label      = row["label"].strip()
        metric_key = _match_pnl_metric_label(label)
        if metric_key is None or metric_key in seen_metrics:
            continue

        values = row.get("values") or []

        if values:
            # Structured path: parse each cell independently
            nums: list[float] = []
            for v in values:
                if v is None:
                    nums.append(0.0)   # treat missing cell as zero
                else:
                    sv = str(v).replace("\n", " ").strip()
                    n  = _parse_num(sv)
                    if n is not None:
                        nums.append(n)
                    else:
                        nums.append(0.0)  # empty or unparseable cell → zero
        else:
            # Text-mode fallback: numbers are embedded in the label string
            nums = _extract_nums(label)

        if len(nums) < expected:
            result["_warnings"].append(
                f"Не удалось разложить строку БДР по проектам и месяцам: {metric_key} "
                f"(найдено {len(nums)} значений, ожидалось {expected})"
            )
            continue

        matrix = _decompose_matrix(nums[:expected], n_months, months, n_projects, projects)
        seen_metrics.add(metric_key)

        for month_key, total in matrix["monthly"].items():
            result["monthly"].setdefault(month_key, {})[metric_key] = total
        for proj, total in matrix["projects"].items():
            result["projects"].setdefault(proj, {})[metric_key] = total
        for proj, month_data in matrix["project_monthly"].items():
            pm = result["project_monthly"].setdefault(proj, {})
            for month_key, val in month_data.items():
                pm.setdefault(month_key, {})[metric_key] = val
        if matrix.get("period_total") is not None:
            result["period_totals"][metric_key] = matrix["period_total"]

    # Cross-check: sum of project totals must equal period total (tolerance ±1 rub)
    _CROSSCHECK_FIELDS = ("revenue", "gross_profit", "operating_expenses", "operating_profit")
    for field in _CROSSCHECK_FIELDS:
        period_total = result["period_totals"].get(field)
        if period_total is None:
            continue
        proj_sum = sum(
            result["projects"].get(proj, {}).get(field) or 0.0
            for proj in projects
        )
        if abs(proj_sum - period_total) > 1.0:
            result["_warnings"].append(
                f"Сумма {field} по проектам ({proj_sum:,.0f}) не сходится с итогом БДР "
                f"({period_total:,.0f}). Возможна ошибка порядка проектов или распознавания таблицы."
            )

    return result


def build_monthly_from_project_monthly(
    project_monthly: dict,
    period: dict,
) -> dict:
    """Sum project values per month. Fallback when monthly totals are unavailable."""
    months: list[str] = period.get("months") or []
    monthly: dict = {}
    for month_key in months:
        vals: dict = {}
        for proj_data in project_monthly.values():
            for field, val in proj_data.get(month_key, {}).items():
                if val is not None:
                    vals[field] = vals.get(field, 0.0) + val
        if vals:
            monthly[month_key] = vals
    return monthly


def build_projects_totals(project_monthly: dict) -> dict:
    """Sum month values per project. Fallback when project totals are unavailable."""
    projects: dict = {}
    for proj, months_data in project_monthly.items():
        vals: dict = {}
        for month_data in months_data.values():
            for field, val in month_data.items():
                if val is not None:
                    vals[field] = vals.get(field, 0.0) + val
        if vals:
            projects[proj] = vals
    return projects
