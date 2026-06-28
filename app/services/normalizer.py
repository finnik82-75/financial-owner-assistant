"""
Нормализация извлечённых данных в каноническую модель БДР / ДДС.
"""
import json
import re
from pathlib import Path

from app.config import settings
from app.services.period_detector import detect_months_from_text, detect_period_label
from app.services.project_detector import detect_projects_from_text
from app.services.pnl_matrix_extractor import extract_pnl_matrix_from_rows
from app.services.quality_checker import check_data_quality
from app.services.kpi_calculator import calculate_owner_kpi
from app.services.analysis_context_builder import build_analysis_context
from app.services.llm_service import generate_owner_report

# ─── Russian labels exported for templates ────────────────────────────────────

PNL_LABELS: dict[str, str] = {
    "revenue":                  "Выручка",
    "cogs":                     "Себестоимость",
    "gross_profit":             "Валовая прибыль",
    "operating_expenses":       "Операционные расходы",
    "payroll":                  "ФОТ (зарплата и взносы)",
    "payroll_related":          "ФОТ-связанные расходы",
    "rent":                     "Аренда",
    "marketing":                "Маркетинг",
    "bank_fees":                "Банковские комиссии",
    "communication":            "Связь",
    "legal_services":           "Юридические услуги",
    "it_expenses":              "ИТ-расходы",
    "depreciation":             "Амортизация",
    "other_operating_expenses": "Прочие операционные расходы",
    "taxes":                    "Налоги",
    "intercompany_turnover":        "ВГО (внутригрупповые)",
    "intercompany_services":        "ВГО-услуги",
    "intercompany_interest":        "ВГО-проценты по займу",
    "operating_profit":             "Прибыль от осн. деятельности",
    "profit_after_other_activity":  "Прибыль с учётом прочей деятельности",
    "ebitda_proxy":                 "EBITDA (proxy)",
    "net_profit":                   "Чистая прибыль",
    "net_profit_proxy":             "Чистая прибыль (proxy)",
}

CASHFLOW_LABELS: dict[str, str] = {
    "cash_start":          "Остаток на начало",
    "operating_inflows":   "Поступления (операционные)",
    "operating_outflows":  "Выплаты (операционные)",
    "operating_cashflow":  "Операционный денежный поток",
    "investment_cashflow": "Инвестиционный поток",
    "financial_cashflow":  "Финансовый поток",
    "net_cashflow":        "Чистый денежный поток",
    "cash_end":            "Остаток на конец",
    "owner_withdrawals":   "Вывод прибыли / дивиденды",
    "advances":            "Авансы",
}

CASHFLOW_DETAIL_LABELS: dict[str, str] = {
    "customer_inflows":               "Поступления от клиентов",
    "media_services_inflows":         "Поступления от медиауслуг",
    "intercompany_operating_inflows": "ВГО (операционная деятельность)",
    "it_communication_services":      "IT, связь, сервисы",
    "bank_fees":                      "Банковские комиссии",
    "payroll":                        "Заработная плата",
    "marketing":                      "Маркетинг и реклама",
    "employee_taxes":                 "Налоги за сотрудников",
    "social_contributions":           "Взносы в фонды",
    "personal_income_tax":            "НДФЛ",
    "income_tax":                     "Налоги на доходы",
    "materials":                      "Оплата за ТМЦ/материалы",
    "contractors":                    "Оплата подрядчикам",
    "other_operating_outflows":       "Прочие операционные выплаты",
    "intercompany_financing":         "ВГО (финансирование)",
}

# ─── Keyword sets for report-type detection ───────────────────────────────────

_PNL_INDICATORS = {
    "выручка", "себестоимость", "валовая прибыль", "валовой доход",
    "чистая прибыль", "фот", "маржа", "ebitda", "прибыль от основной",
}
_CASHFLOW_INDICATORS = {
    "остаток на начало", "остаток на конец", "денежный поток", "ддс",
    "кассовый", "поступления", "остаток денег",
}

# ─── Labels to silently skip ──────────────────────────────────────────────────
# Checked by exact match AND as leading word (e.g. "итого по разделу").

_SKIP_LABELS: frozenset[str] = frozenset({
    "статья", "показатель", "наименование", "категория",
    "итого", "всего", "в том числе", "из них", "сумма", "план", "факт",
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "q1", "q2", "q3", "q4",
    "вид деятельности",         # column header in PDF ДДС
})

# Rows matching these keywords with no numeric data are silently ignored
# (org names, section headers, group titles, etc.)
_IGNORE_KEYWORDS: frozenset[str] = frozenset({
    "медиагруппа",
    " ооо ", " ао ", " зао ", " оао ", " пао ", " нко ", " ип ",
    "холдинг",
    "группа компаний",
})

# Cashflow totals that must NOT be sourced from nested sub-lines (·· ···)
_CASHFLOW_AGGREGATE_FIELDS: frozenset[str] = frozenset({
    "operating_inflows", "operating_outflows", "operating_cashflow",
    "investment_cashflow", "financial_cashflow", "net_cashflow",
})

# Sub-keywords that indicate a specific cashflow sub-category, not the total outflows.
# A row containing any of these should not be mapped to operating_outflows.
_OUTFLOWS_EXCLUSIONS: frozenset[str] = frozenset({
    "банковские комиссии",
    "ндфл",
    "оплата подрядчикам",
    "оплата за тмц",
    "прочие операционные выплаты",
    "услуги",
})

# Patterns for cashflow detail sub-lines (·· ···).
# Ordered most-specific → least-specific (same rule as _MAPPING).
# Patterns are post-normalization (commas → spaces, lowercase).
_CASHFLOW_DETAIL_MAPPING: list[tuple[str, str]] = [
    ("поступления от медиауслуг",          "media_services_inflows"),
    ("поступления от клиентов",            "customer_inflows"),
    ("вго по операционной деятельности",   "intercompany_operating_inflows"),
    ("вго по операционной",                "intercompany_operating_inflows"),
    ("вго финансирование",                 "intercompany_financing"),
    ("it связь сервисы",                   "it_communication_services"),
    ("ит связь сервисы",                   "it_communication_services"),
    ("налоги за сотрудников",              "employee_taxes"),
    ("взносы в фонды",                     "social_contributions"),
    ("страховые взносы",                   "social_contributions"),
    ("банковские комиссии",                "bank_fees"),
    ("заработная плата",                   "payroll"),
    ("зарплата",                           "payroll"),
    ("маркетинг и реклама",                "marketing"),
    ("маркетинг",                          "marketing"),
    ("реклама",                            "marketing"),
    ("ндфл",                               "personal_income_tax"),
    ("налоги на доходы",                   "income_tax"),
    ("налог на прибыль",                   "income_tax"),
    ("оплата за тмц",                      "materials"),
    ("расходные материалы",                "materials"),
    ("оплата подрядчикам",                 "contractors"),
    ("исполнителям за услуги",             "contractors"),
    ("прочие операционные выплаты",        "other_operating_outflows"),
]

# Fields extracted per month from multi-period cashflow documents.
# Ordered most-specific → least-specific (same precedence rule as _MAPPING).
_MONTHLY_CF_PATTERNS: list[tuple[str, str]] = [
    ("операционный денежный поток",    "operating_cashflow"),
    ("операционный поток",             "operating_cashflow"),
    (" операционная ",                 "operating_cashflow"),
    ("инвестиционный денежный поток",  "investment_cashflow"),
    ("инвестиционный поток",           "investment_cashflow"),
    (" инвестиционная ",               "investment_cashflow"),
    ("финансовый денежный поток",      "financial_cashflow"),
    ("финансовый поток",               "financial_cashflow"),
    (" финансовая ",                   "financial_cashflow"),
    ("чистый денежный поток",          "net_cashflow"),
    ("чистый поток",                   "net_cashflow"),
    ("денег на конец периода",         "cash_end"),
    ("остаток денег на конец",         "cash_end"),
    ("остаток на конец периода",       "cash_end"),
    ("остаток на конец",               "cash_end"),
    ("денег на конец",                 "cash_end"),
]

# Detects nested bullet sub-lines (·· ···) — rows that are detail breakdowns,
# not top-level cashflow totals
_SUBLINE_RE = re.compile(r"^[·•▪]{2,}")

# Ordered list of (bad, good) replacements for OCR-split artifacts.
# Applied after lowercasing; more specific entries go first.
_OCR_FIXES: list[tuple[str, str]] = [
    ("с у четом",           "с учетом"),           # must precede "у четом"
    ("у четом",             "учетом"),              # instrumental: "с учетом"
    ("у чета",              "учета"),               # genitive: "без учета"
    ("осн. деятельности",   "основной деятельности"),
]

# Regex for extracting money-like numbers from mixed text+number strings.
# Handles Russian space-separated thousands and comma/dot decimals:
#   "1 342 332,41"  "-1 066 222,95"  "-11643601.27"  "1 050 000"
#
# (?!\d) after the spaced-thousands branch prevents "0 105" from being
# matched as 105 when "0" and the start of "1050000" happen to be adjacent
# (e.g. "0 0 1050000" → "0", "0", "1050000", not "0", "0 105", "000").
_EMBEDDED_NUMBER_RE = re.compile(
    r"-?\d{1,3}(?:[ \xa0]\d{3})+(?:[,.]\d+)?(?!\d)"   # spaced thousands, no trailing digit
    r"|-?\d+(?:[,.]\d+)?"                               # plain integer or decimal
)

# ─── Pattern → (canonical_name, primary_section) ─────────────────────────────
# Ordered most-specific → least-specific.
# Patterns with surrounding spaces (e.g. " фот ") match whole words only
# because map_raw_line_to_canonical pads the normalized text before matching.

_MAPPING: list[tuple[str, str, str]] = [
    # ── PnL: profit lines (most specific first) ──
    ("прибыль с учетом прочей деятельности", "profit_after_other_activity", "pnl"),
    ("прибыль без учета амортизации",         "ebitda_proxy",               "pnl"),
    ("ebitda",                                "ebitda_proxy",               "pnl"),
    ("прибыль от основной деятельности",      "operating_profit",           "pnl"),
    ("прибыль от продаж",                     "operating_profit",           "pnl"),
    ("операционная прибыль",                  "operating_profit",           "pnl"),
    ("ebit",                                  "operating_profit",           "pnl"),
    ("чистая прибыль",                        "net_profit",                 "pnl"),

    # ── PnL: gross profit (must be before generic "доход" revenue patterns) ──
    ("валовая прибыль",                  "gross_profit",             "pnl"),
    ("валовой доход",                    "gross_profit",             "pnl"),
    ("валовый доход",                    "gross_profit",             "pnl"),

    # ── PnL: revenue ──
    ("выручка от реализации",            "revenue",                  "pnl"),
    ("выручка от продаж",                "revenue",                  "pnl"),
    ("доходы от рекламы",                "revenue",                  "pnl"),
    ("доходы от услуг",                  "revenue",                  "pnl"),
    ("выручка",                          "revenue",                  "pnl"),
    ("доходы",                           "revenue",                  "pnl"),
    ("доход",                            "revenue",                  "pnl"),

    # ── PnL: cost of goods ──
    ("себестоимость продаж",             "cogs",                     "pnl"),
    ("себестоимость реализации",         "cogs",                     "pnl"),
    ("себестоимость",                    "cogs",                     "pnl"),
    ("прямые затраты",                   "cogs",                     "pnl"),

    # ── PnL: payroll-related (specific — before generic payroll) ──
    ("отчисления в фонды",               "payroll_related",          "pnl"),
    ("страховые взносы",                 "payroll_related",          "pnl"),
    ("охрана труда",                     "payroll_related",          "pnl"),
    ("подбор персонала",                 "payroll_related",          "pnl"),
    ("обучение персонала",               "payroll_related",          "pnl"),
    ("подбор",                           "payroll_related",          "pnl"),
    ("обучение",                         "payroll_related",          "pnl"),
    ("взносы",                           "payroll_related",          "pnl"),

    # ── PnL: payroll ──
    ("отчисления с фот",                 "payroll",                  "pnl"),
    ("оплата труда",                     "payroll",                  "pnl"),
    ("заработная плата",                 "payroll",                  "pnl"),
    ("зарплата",                         "payroll",                  "pnl"),
    (" фот ",                            "payroll",                  "pnl"),

    # ── PnL: rent ──
    ("арендная плата",                   "rent",                     "pnl"),
    ("аренда",                           "rent",                     "pnl"),

    # ── PnL: taxes ──
    ("налог на прибыль",                 "taxes",                    "pnl"),
    (" усн ",                            "taxes",                    "pnl"),
    ("налоги",                           "taxes",                    "pnl"),
    ("налог",                            "taxes",                    "pnl"),

    # ── PnL: marketing ──
    ("маркетинговые",                    "marketing",                "pnl"),
    ("маркетинг",                        "marketing",                "pnl"),
    ("реклама",                          "marketing",                "pnl"),

    # ── PnL: bank fees ──
    ("расчетно-кассовое",                "bank_fees",                "pnl"),
    ("услуги банка",                     "bank_fees",                "pnl"),
    ("эквайринг",                        "bank_fees",                "pnl"),
    (" банк ",                           "bank_fees",                "pnl"),

    # ── PnL: communication ──
    ("услуги связи",                     "communication",            "pnl"),
    ("телефония",                        "communication",            "pnl"),
    ("интернет",                         "communication",            "pnl"),
    (" связь ",                          "communication",            "pnl"),

    # ── PnL: legal services ──
    ("юридические услуги",               "legal_services",           "pnl"),
    ("правовое сопровождение",           "legal_services",           "pnl"),
    ("юрист",                            "legal_services",           "pnl"),

    # ── PnL: IT expenses ──
    ("сопровождение программных систем", "it_expenses",              "pnl"),
    ("программных систем",               "it_expenses",              "pnl"),
    ("доменных имен",                    "it_expenses",              "pnl"),
    (" ит ",                             "it_expenses",              "pnl"),

    # ── PnL: depreciation ──
    ("амортизация",                      "depreciation",             "pnl"),

    # ── PnL: other operating (specific before generic) ──
    ("услуги сторонних организаций",     "other_operating_expenses", "pnl"),
    ("услуги звукозаписи",               "other_operating_expenses", "pnl"),
    ("почтовые услуги",                  "other_operating_expenses", "pnl"),
    ("лицензионные платежи",             "other_operating_expenses", "pnl"),
    ("кртпц",                            "other_operating_expenses", "pnl"),
    ("спср",                             "other_operating_expenses", "pnl"),
    ("рао",                              "other_operating_expenses", "pnl"),
    ("воис",                             "other_operating_expenses", "pnl"),
    ("ккм",                              "other_operating_expenses", "pnl"),
    ("касс",                             "other_operating_expenses", "pnl"),
    ("почта",                            "other_operating_expenses", "pnl"),

    # ── PnL: broad operating expenses (after all specifics) ──
    ("коммерческие расходы",             "operating_expenses",       "pnl"),
    ("административные расходы",         "operating_expenses",       "pnl"),
    ("управленческие расходы",           "operating_expenses",       "pnl"),
    ("операционные расходы",             "operating_expenses",       "pnl"),
    ("прочие расходы",                   "operating_expenses",       "pnl"),
    ("расходы",                          "operating_expenses",       "pnl"),

    # ── Intercompany (specific sub-types before generic ВГО) ──
    ("вго-услуги",                       "intercompany_services",    "pnl"),
    ("вго услуги",                       "intercompany_services",    "pnl"),
    ("вго-%",                            "intercompany_interest",    "pnl"),
    ("вго процент",                      "intercompany_interest",    "pnl"),
    ("вго по займу",                     "intercompany_interest",    "pnl"),
    ("процент по займу",                 "intercompany_interest",    "pnl"),
    ("внутригрупповой оборот",           "intercompany_turnover",    "pnl"),
    ("внутригрупповые",                  "intercompany_turnover",    "pnl"),
    (" вго ",                            "intercompany_turnover",    "pnl"),

    # ── Cashflow ──
    ("остаток денег на начало",          "cash_start",               "cashflow"),
    ("остаток средств на начало",        "cash_start",               "cashflow"),
    ("остаток на начало периода",        "cash_start",               "cashflow"),
    ("остаток на начало",                "cash_start",               "cashflow"),
    ("начальный остаток",                "cash_start",               "cashflow"),
    ("остаток денег на конец",           "cash_end",                 "cashflow"),
    ("остаток средств на конец",         "cash_end",                 "cashflow"),
    ("остаток на конец периода",         "cash_end",                 "cashflow"),
    ("остаток на конец",                 "cash_end",                 "cashflow"),
    ("конечный остаток",                 "cash_end",                 "cashflow"),
    ("денег на конец",                   "cash_end",                 "cashflow"),
    ("денег на начало",                  "cash_start",               "cashflow"),
    ("операционные поступления",         "operating_inflows",        "cashflow"),
    ("поступления от клиентов",          "operating_inflows",        "cashflow"),
    ("поступления от покупателей",       "operating_inflows",        "cashflow"),
    ("поступления",                      "operating_inflows",        "cashflow"),
    ("приход",                           "operating_inflows",        "cashflow"),
    ("операционные выплаты",             "operating_outflows",       "cashflow"),
    ("выплаты поставщикам",              "operating_outflows",       "cashflow"),
    ("выплаты подрядчикам",              "operating_outflows",       "cashflow"),
    ("выплаты",                          "operating_outflows",       "cashflow"),
    ("списани",                          "operating_outflows",       "cashflow"),
    ("расход",                           "operating_outflows",       "cashflow"),
    ("операционный денежный поток",      "operating_cashflow",       "cashflow"),
    ("операционный поток",               "operating_cashflow",       "cashflow"),
    (" операционная ",                   "operating_cashflow",       "cashflow"),
    ("инвестиционный денежный поток",    "investment_cashflow",      "cashflow"),
    ("инвестиционный поток",             "investment_cashflow",      "cashflow"),
    (" инвестиционная ",                 "investment_cashflow",      "cashflow"),
    ("финансовый денежный поток",        "financial_cashflow",       "cashflow"),
    ("финансовый поток",                 "financial_cashflow",       "cashflow"),
    (" финансовая ",                     "financial_cashflow",       "cashflow"),
    ("чистый денежный поток",            "net_cashflow",             "cashflow"),
    ("чистый поток",                     "net_cashflow",             "cashflow"),
    ("выплата дивидендов",               "owner_withdrawals",        "cashflow"),
    ("вывод прибыли",                    "owner_withdrawals",        "cashflow"),
    ("изъятие прибыли",                  "owner_withdrawals",        "cashflow"),
    ("дивиденды",                        "owner_withdrawals",        "cashflow"),
    ("авансовые платежи",                "advances",                 "cashflow"),
    ("авансы",                           "advances",                 "cashflow"),
    ("аванс",                            "advances",                 "cashflow"),
]

# Canonical field → output section
_FIELD_SECTION: dict[str, str] = {
    "revenue":                  "pnl",
    "cogs":                     "pnl",
    "gross_profit":             "pnl",
    "operating_expenses":       "pnl",
    "payroll":                  "pnl",
    "payroll_related":          "pnl",
    "rent":                     "pnl",
    "marketing":                "pnl",
    "bank_fees":                "pnl",
    "communication":            "pnl",
    "legal_services":           "pnl",
    "it_expenses":              "pnl",
    "depreciation":             "pnl",
    "other_operating_expenses": "pnl",
    "taxes":                    "pnl",
    "intercompany_turnover":        "pnl",
    "intercompany_services":        "pnl",
    "intercompany_interest":        "pnl",
    "operating_profit":             "pnl",
    "profit_after_other_activity":  "pnl",
    "ebitda_proxy":                 "pnl",
    "net_profit":                   "pnl",
    "net_profit_proxy":             "pnl",
    "cash_start":               "cashflow",
    "operating_inflows":        "cashflow",
    "operating_outflows":       "cashflow",
    "operating_cashflow":       "cashflow",
    "investment_cashflow":      "cashflow",
    "financial_cashflow":       "cashflow",
    "net_cashflow":             "cashflow",
    "cash_end":                 "cashflow",
    "owner_withdrawals":        "cashflow",
    "advances":                 "cashflow",
}


# ─── Private helpers ──────────────────────────────────────────────────────────

def _empty_pnl() -> dict:
    return {
        "revenue":                  None,
        "cogs":                     None,
        "gross_profit":             None,
        "operating_expenses":       None,
        "payroll":                  None,
        "payroll_related":          None,
        "rent":                     None,
        "marketing":                None,
        "bank_fees":                None,
        "communication":            None,
        "legal_services":           None,
        "it_expenses":              None,
        "depreciation":             None,
        "other_operating_expenses": None,
        "taxes":                    None,
        "intercompany_turnover":        None,
        "intercompany_services":        None,
        "intercompany_interest":        None,
        "operating_profit":             None,
        "profit_after_other_activity":  None,
        "ebitda_proxy":                 None,
        "ebitda_proxy_source":          None,   # "extracted" | "calculated" | None
        "net_profit":                   None,
        "net_profit_proxy":               None,
        "uses_net_profit_proxy":          False,
        "gross_profit_calculated":        False,
        "operating_profit_calculated":    False,
        # Multi-period / project analysis
        "monthly":         {},   # {"2026-01": {field: value, ...}, ...}
        "projects":        {},   # {"project_name": {field: value, ...}, ...}
        "project_monthly": {},   # {"project_name": {"2026-01": {field: value, ...}}}
        "period_totals":   {},   # {field: grand_total_for_period}
    }


def _empty_cashflow_details() -> dict:
    return {
        "customer_inflows":               None,
        "media_services_inflows":         None,
        "intercompany_operating_inflows": None,
        "it_communication_services":      None,
        "bank_fees":                      None,
        "payroll":                        None,
        "marketing":                      None,
        "employee_taxes":                 None,
        "social_contributions":           None,
        "personal_income_tax":            None,
        "income_tax":                     None,
        "materials":                      None,
        "contractors":                    None,
        "other_operating_outflows":       None,
        "intercompany_financing":         None,
    }


def _empty_cashflow() -> dict:
    return {
        "cash_start":          None,
        "operating_inflows":   None,
        "operating_outflows":  None,
        "operating_cashflow":  None,
        "investment_cashflow": None,
        "financial_cashflow":  None,
        "net_cashflow":        None,
        "cash_end":            None,
        "owner_withdrawals":   None,
        "advances":            None,
        # Source / calculation flags
        "operating_inflows_source":  None,   # "extracted" | "heuristic" | None
        "operating_outflows_source": None,
        "operating_cashflow_source": None,
        "financial_cashflow_source": None,
        "cash_end_source":           None,
        "net_cashflow_calculated":   False,
        "net_cashflow_source":       None,   # "calculated_from_activity_cashflows" | "calculated_from_balances"
        "cash_start_calculated":     False,
        "cash_start_source":         None,   # "calculated_from_cash_end_and_net_cashflow"
        # Detail breakdown from sub-lines (·· ···)
        "cashflow_details": _empty_cashflow_details(),
        # Monthly breakdown (populated by future multi-period parsers)
        "monthly": {},   # {"2026-01": {field: value, ...}, ...}
    }


def _normalize_text(s: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation (keep hyphen)."""
    s = s.lower().strip()
    s = re.sub(r"[«»\"'(){}\[\]/:;,!?*]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_skip_label(norm: str) -> bool:
    """True for header rows and noise that should be silently discarded."""
    if norm in _SKIP_LABELS:
        return True
    first_word = norm.split()[0] if norm.split() else ""
    if first_word in _SKIP_LABELS:
        return True
    # Multi-word skip labels: match as a prefix (e.g. "вид деятельности Январь...")
    for label in _SKIP_LABELS:
        if " " in label and norm.startswith(label):
            return True
    return False


def _is_ignorable_label(norm: str, values: list) -> bool:
    """
    True for org-name / group-title rows with no numeric data.
    e.g. "ООО Медиагруппа ЗМГ" with an empty value column.
    """
    padded = f" {norm} "
    if not any(kw in padded for kw in _IGNORE_KEYWORDS):
        return False
    # Suppress only if the row contains no parseable numbers
    return not any(
        _parse_number(str(v)) is not None
        for v in values
        if v is not None and str(v).strip()
    )


def _is_cashflow_subline(label: str) -> bool:
    """
    True for nested bullet sub-lines (·· ···) in PDF cashflow reports.
    These are detail breakdowns of a top-level category and must not
    be used to populate aggregate cashflow totals.
    """
    return bool(_SUBLINE_RE.match(label.strip()))


def _is_percentage_only_line(label: str) -> bool:
    """True when a line contains only percentages, digits, and whitespace (no letters)."""
    stripped = label.strip()
    return bool(stripped) and "%" in stripped and not re.search(r"[a-zA-Zа-яёА-ЯЁ]", stripped)


def _map_to_cashflow_detail(label: str) -> str | None:
    """
    Map a sub-line label to a cashflow_details field name.
    Returns None when no pattern matches.
    """
    cleaned    = clean_raw_line_for_mapping(label)
    normalized = _normalize_text(cleaned)
    padded     = f" {normalized} "
    for pattern, detail_field in _CASHFLOW_DETAIL_MAPPING:
        if pattern in padded:
            return detail_field
    return None


def _assign_detail(normalized: dict, detail_field: str, value: float | None) -> None:
    """Write value to cashflow_details; never overwrites an existing value."""
    if value is None:
        return
    details = normalized["cashflow"].get("cashflow_details")
    if details is None:
        return
    if details.get(detail_field) is None:
        details[detail_field] = value


def clean_raw_line_for_mapping(raw_line: str) -> str:
    """
    Pre-process a raw label before canonical mapping:
    lowercase, collapse whitespace, then repair known OCR-split artifacts.
    """
    s = raw_line.lower()
    s = re.sub(r"\s+", " ", s).strip()
    for bad, good in _OCR_FIXES:
        s = s.replace(bad, good)
    return s


def _parse_number(raw: str) -> float | None:
    """Parse a Russian/European formatted number string into float."""
    if raw is None:
        return None
    s = str(raw).strip()

    if not s or s in ("-", "—", "–", "н/д", "нет", "null", "none", "0-"):
        return None

    # Accounting negative: (1 234 567)
    is_negative = False
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1].strip()
        is_negative = True
    elif s.startswith("-"):
        is_negative = True
        s = s[1:].strip()

    if "%" in s:
        return None

    s = re.sub(r"(руб\.?|тыс\.?|млн\.?|млрд\.?|[₽$€£])", "", s, flags=re.IGNORECASE)
    s = s.strip()

    s = s.replace(" ", "").replace("\xa0", "").replace(" ", "")

    if not s:
        return None

    dots   = s.count(".")
    commas = s.count(",")

    if dots > 1 and commas == 0:
        s = s.replace(".", "")
    elif dots == 1 and commas == 1:
        dot_pos   = s.index(".")
        comma_pos = s.index(",")
        if dot_pos < comma_pos:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif commas == 1 and dots == 0:
        s = s.replace(",", ".")
    elif commas > 1 and dots == 0:
        s = s.replace(",", "")

    try:
        val = float(s)
        return -val if is_negative else val
    except ValueError:
        return None


def _pick_value_from_row(values: list) -> float | None:
    """
    Return the single "main" value from a row's value cells.
    Scans right-to-left and returns the first non-zero parseable number
    (rightmost column is usually the total or most recent period).
    """
    parsed = []
    for v in values:
        if v is None:
            continue
        n = _parse_number(str(v))
        if n is not None:
            parsed.append(n)

    if not parsed:
        return None
    for v in reversed(values):
        if v is None:
            continue
        n = _parse_number(str(v))
        if n is not None and n != 0.0:
            return n
    return parsed[-1]


def _assign(normalized: dict, canonical: str, value: float | None) -> None:
    """Write value into the correct section; never overwrites an existing value."""
    if value is None:
        return
    section = _FIELD_SECTION.get(canonical)
    if section == "pnl":
        if normalized["pnl"].get(canonical) is None:
            normalized["pnl"][canonical] = value
    elif section == "cashflow":
        if normalized["cashflow"].get(canonical) is None:
            normalized["cashflow"][canonical] = value


def _apply_derived_logic(normalized: dict) -> None:
    """
    Calculate missing PnL values from available data, apply net_profit proxy,
    and emit warnings for missing report sections.
    """
    pnl = normalized["pnl"]

    # 1. Derive gross_profit = revenue - cogs
    if pnl["gross_profit"] is None:
        if pnl["revenue"] is not None and pnl["cogs"] is not None:
            pnl["gross_profit"] = pnl["revenue"] - pnl["cogs"]
            pnl["gross_profit_calculated"] = True
            normalized["warnings"].append(
                "Валовая прибыль не найдена в отчёте и рассчитана "
                "как выручка минус себестоимость."
            )

    # 2. Derive operating_profit = gross_profit - operating_expenses
    if pnl["operating_profit"] is None:
        if pnl["gross_profit"] is not None and pnl["operating_expenses"] is not None:
            pnl["operating_profit"] = pnl["gross_profit"] - pnl["operating_expenses"]
            pnl["operating_profit_calculated"] = True

    # 3. ebitda_proxy: mark source or calculate fallback
    if pnl["ebitda_proxy"] is not None:
        # Value came from the document via _assign → mark as extracted
        pnl["ebitda_proxy_source"] = "extracted"
    elif pnl["operating_profit"] is not None and pnl["depreciation"] is not None:
        # EBITDA = operating_profit + D&A (adding back the non-cash charge)
        pnl["ebitda_proxy"] = pnl["operating_profit"] + pnl["depreciation"]
        pnl["ebitda_proxy_source"] = "calculated"

    # 4. net_profit proxy from operating_profit
    if pnl["net_profit"] is None and pnl["operating_profit"] is not None:
        pnl["net_profit_proxy"] = pnl["operating_profit"]
        pnl["uses_net_profit_proxy"] = True
        normalized["warnings"].append(
            "Чистая прибыль не найдена. "
            "«Прибыль от основной деятельности» используется как приближение (proxy)."
        )

    # ── Cashflow derived values ──
    cf = normalized.get("cashflow")
    if cf is None:
        cf = _empty_cashflow()
        normalized["cashflow"] = cf

    # Mark fields that already have values as "extracted" from the document
    for _field, _src in (
        ("operating_inflows",  "operating_inflows_source"),
        ("operating_outflows", "operating_outflows_source"),
        ("operating_cashflow", "operating_cashflow_source"),
        ("financial_cashflow", "financial_cashflow_source"),
        ("cash_end",           "cash_end_source"),
    ):
        if cf[_field] is not None and cf[_src] is None:
            cf[_src] = "extracted"

    # ── operating_outflows: correct or derive from inflows + operating cashflow ──
    # Rules (applied in order):
    #   1. If an extracted value is inconsistent with operating_cashflow, override it.
    #   2. If no extracted value but both inflows and operating_cashflow are known, derive it.
    # Source is set to "calculated_from_inflows_and_operating_cashflow" in both cases.
    if cf["operating_inflows"] is not None and cf["operating_cashflow"] is not None:
        _calculated_outflows = cf["operating_cashflow"] - cf["operating_inflows"]

        if cf["operating_outflows"] is None:
            cf["operating_outflows"] = _calculated_outflows
            cf["operating_outflows_source"] = "calculated_from_inflows_and_operating_cashflow"
        else:
            _implied = cf["operating_inflows"] + cf["operating_outflows"]
            if abs(_implied - cf["operating_cashflow"]) > 1:
                cf["operating_outflows"] = _calculated_outflows
                cf["operating_outflows_source"] = "calculated_from_inflows_and_operating_cashflow"

    # Priority 1: always calculate net_cashflow from activity cashflows.
    # This deliberately overrides any value that may have been extracted directly
    # from the document, because individual-period rows can be picked up
    # instead of the full-quarter total.
    has_activity_cashflows = (
        cf["operating_cashflow"] is not None
        or cf["financial_cashflow"] is not None
        or cf["investment_cashflow"] is not None
    )
    if has_activity_cashflows:
        op  = cf["operating_cashflow"]  or 0.0
        inv = cf["investment_cashflow"] or 0.0
        fin = cf["financial_cashflow"]  or 0.0
        cf["net_cashflow"] = op + inv + fin
        cf["net_cashflow_calculated"] = True
        cf["net_cashflow_source"] = "calculated_from_activity_cashflows"
    elif (cf["cash_start"] is not None
          and cf["cash_end"] is not None
          and cf["net_cashflow"] is None):
        # Priority 2: derive from balance change (only when no activity cashflows)
        cf["net_cashflow"] = cf["cash_end"] - cf["cash_start"]
        cf["net_cashflow_calculated"] = True
        cf["net_cashflow_source"] = "calculated_from_balances"

    # Derive cash_start = cash_end - net_cashflow (uses the final net_cashflow above)
    if (cf["cash_start"] is None
            and cf["cash_end"] is not None
            and cf["net_cashflow"] is not None):
        cf["cash_start"] = cf["cash_end"] - cf["net_cashflow"]
        cf["cash_start_calculated"] = True
        cf["cash_start_source"] = "calculated_from_cash_end_and_net_cashflow"

    # Inconsistency warning — only when operating_outflows was NOT recalculated.
    # (When it was recalculated the three values are algebraically consistent by construction.)
    if (cf["operating_inflows"] is not None
            and cf["operating_outflows"] is not None
            and cf["operating_cashflow"] is not None
            and cf["operating_outflows_source"]
                != "calculated_from_inflows_and_operating_cashflow"):
        implied = cf["operating_inflows"] + cf["operating_outflows"]
        if abs(implied - cf["operating_cashflow"]) > 1:
            normalized["warnings"].append(
                "Операционный денежный поток не сходится с поступлениями и выплатами. "
                "Проверьте структуру ДДС."
            )

    # 4. Warn about missing report sections
    detected = normalized["detected_reports"]
    if "pnl" in detected and "cashflow" not in detected:
        normalized["warnings"].append(
            "ДДС не обнаружен. "
            "Вывод о деньгах, ликвидности и кассовом разрыве будет ограничен."
        )
    elif "cashflow" in detected and "pnl" not in detected:
        normalized["warnings"].append(
            "БДР не обнаружен. "
            "Вывод о прибыли, марже и рентабельности будет ограничен."
        )


# ─── Public API ───────────────────────────────────────────────────────────────

def load_parsed_json(parsed_file_path: str) -> dict:
    return json.loads(Path(parsed_file_path).read_text(encoding="utf-8"))


def get_file_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def extract_rows_from_document(document: dict) -> list[dict]:
    """
    Flatten a parsed document into a list of
    {"label": str, "values": list, "source": str} dicts.
    """
    rows: list[dict] = []
    file_type = document.get("file_type")

    if file_type == "excel":
        for sheet in document.get("sheets", []):
            for record in sheet.get("records", []):
                if not record:
                    continue
                vals  = list(record.values())
                label = str(vals[0]).strip() if vals else ""
                values = vals[1:] if len(vals) > 1 else []
                if label:
                    rows.append({
                        "label":  label,
                        "values": values,
                        "source": f"excel:{sheet.get('sheet_name', '')}",
                    })

    elif file_type == "pdf":
        for page in document.get("pages", []):
            page_num = page.get("page_number", "?")
            for t_idx, table in enumerate(page.get("tables", [])):
                for row in table:
                    if not row:
                        continue
                    label  = str(row[0]).strip() if row[0] is not None else ""
                    values = row[1:]
                    if label:
                        rows.append({
                            "label":  label,
                            "values": values,
                            "source": f"pdf:page{page_num}:table{t_idx}",
                        })

            # Fallback: text lines when page has no tables
            if not page.get("tables") and page.get("text"):
                for line in page["text"].split("\n"):
                    line = line.strip()
                    if line:
                        rows.append({
                            "label":  line,
                            "values": [],
                            "source": f"pdf:page{page_num}:text",
                        })

    return rows


def detect_report_type(text_or_rows: list) -> str:
    """
    Detect whether the rows describe a PnL, cashflow, mixed, or unknown report.
    """
    if not text_or_rows:
        return "unknown"

    parts: list[str] = []
    for item in text_or_rows:
        if isinstance(item, dict):
            parts.append(" ".join(str(v) for v in item.values()))
        else:
            parts.append(str(item))

    text = " ".join(parts).lower()

    has_pnl = any(kw in text for kw in _PNL_INDICATORS)
    has_cf  = any(kw in text for kw in _CASHFLOW_INDICATORS)

    if has_pnl and has_cf:
        return "mixed"
    if has_pnl:
        return "pnl"
    if has_cf:
        return "cashflow"
    return "unknown"


def map_raw_line_to_canonical(raw_line: str) -> str | None:
    """
    Map a raw row label to a canonical field name via substring matching.
    The normalized text is padded with spaces so spaced patterns (e.g. " фот ")
    match whole words only. Returns None if no pattern matches.
    """
    if not raw_line:
        return None

    cleaned    = clean_raw_line_for_mapping(raw_line)
    normalized = _normalize_text(cleaned)

    if _is_skip_label(normalized):
        return None

    # Pad once; patterns with leading/trailing spaces get whole-word matching.
    padded = f" {normalized} "

    for pattern, canonical, _section in _MAPPING:
        if pattern in padded:
            return canonical

    return None


def extract_numeric_values(row) -> list[float]:
    """Extract all parseable numeric values from a row (dict, list, or string)."""
    if isinstance(row, dict):
        items = list(row.values())
    elif isinstance(row, (list, tuple)):
        items = list(row)
    else:
        items = [str(row)]

    result: list[float] = []
    for item in items:
        if item is None:
            continue
        parsed = _parse_number(str(item))
        if parsed is not None:
            result.append(parsed)
    return result


def _extract_numbers_from_string(text: str) -> list[float]:
    """
    Extract all money-like numbers from a mixed text+number string.
    Returns them left-to-right; rightmost is typically the total/cumulative.
    Used for PDF text-mode rows where the full line becomes the label.
    """
    text = re.sub(r"^[·•\-–—]+\s*", "", text).strip()
    results: list[float] = []
    for m in _EMBEDDED_NUMBER_RE.finditer(text):
        raw = m.group()
        end = m.end()
        if end < len(text) and text[end] == "%":
            # The spaced-thousands branch greedily included the last " NNN" group
            # that belongs to a "NNN%" percentage token.  Strip that group and
            # try to keep the rest (e.g. "1 050 000 100%" → keep "1 050 000").
            trimmed = re.sub(r"[ \xa0]\d{3}$", "", raw)
            if trimmed and trimmed != raw:
                n = _parse_number(trimmed)
                if n is not None:
                    results.append(n)
            continue
        n = _parse_number(raw)
        if n is not None:
            results.append(n)
    return results


def _is_numbers_only(norm: str) -> bool:
    """True when the normalized label contains only digits, spaces, dots, commas, signs."""
    return bool(norm) and bool(re.match(r"^[-\d\s.,]+$", norm))


def _collect_all_values_from_row(row: dict) -> list[float]:
    """Extract all numeric values from a row; falls back to numbers embedded in label."""
    values = row.get("values") or []
    if values:
        result: list[float] = []
        for v in values:
            if v is None:
                continue
            n = _parse_number(str(v))
            if n is not None:
                result.append(n)
        return result
    return _extract_numbers_from_string(row["label"])


def _match_monthly_cf_pattern(label: str) -> str | None:
    """Return the cashflow field name if label matches a monthly cashflow pattern."""
    cleaned    = clean_raw_line_for_mapping(label)
    normalized = _normalize_text(cleaned)
    padded     = f" {normalized} "
    for pattern, field in _MONTHLY_CF_PATTERNS:
        if pattern in padded:
            return field
    return None


def _assign_monthly(
    monthly: dict,
    field: str,
    nums: list[float],
    n_months: int,
    months: list[str],
    warnings: list,
) -> None:
    """Write per-month values into monthly dict (last write wins)."""
    if len(nums) == n_months + 1:
        monthly_nums = nums[:n_months]      # first n_months monthly, last is total
    elif len(nums) >= n_months:
        monthly_nums = nums[:n_months]
    else:
        warnings.append(
            f"Помесячный ДДС: «{field}» — найдено {len(nums)} значений, "
            f"ожидалось {n_months}. Строка пропущена."
        )
        return
    for i, month_key in enumerate(months):
        if month_key not in monthly:
            monthly[month_key] = {}
        monthly[month_key][field] = monthly_nums[i]     # last write wins


def _extract_monthly_cashflow(
    rows: list[dict],
    n_months: int,
    months: list[str],
    warnings: list,
) -> dict:
    """
    Extract per-month values for key cashflow fields from one document's rows.

    Works in two modes:
    - Inline: label + numbers in the same row (PDF text-fallback or Excel)
    - Pair:   label row with no numbers, followed by a numeric-only row

    Percentage-only rows are transparent; sub-lines (·· ···) are ignored.
    Semantics: last write wins — later rows overwrite earlier for the same field/month.
    """
    if n_months == 0 or not months:
        return {}

    monthly: dict[str, dict[str, float]] = {}
    pending_field: str | None = None

    for row in rows:
        label      = row["label"].strip()
        norm_label = _normalize_text(label)

        if not norm_label or _is_skip_label(norm_label):
            pending_field = None
            continue

        if _is_percentage_only_line(label):
            continue                     # transparent — don't clear pending

        if _is_cashflow_subline(label):
            pending_field = None
            continue

        # Pair mode: pending label waiting for a following numeric-only row
        if pending_field is not None:
            if _is_numbers_only(norm_label):
                nums = _collect_all_values_from_row(row)
                if nums:
                    _assign_monthly(monthly, pending_field, nums, n_months, months, warnings)
                pending_field = None
                continue
            pending_field = None         # non-numeric row broke the pair

        field = _match_monthly_cf_pattern(label)
        if field is None:
            continue

        nums = _collect_all_values_from_row(row)
        if not nums:
            pending_field = field        # inline numbers absent — wait for next row
            continue

        _assign_monthly(monthly, field, nums, n_months, months, warnings)

    return monthly


def _collect_text_from_parsed(parsed_result: dict) -> str:
    """
    Collect all human-readable text from parsed documents.

    For period detection: page text and row labels are sufficient.
    For project detection: PDF table column headers live in *values* (non-label
    cells), so we must collect ALL table cells, not just the first column.
    """
    parts: list[str] = []
    for doc in parsed_result.get("documents", []):
        if doc.get("status") == "error":
            continue
        parts.append(doc.get("original_filename", ""))
        file_type = doc.get("file_type")

        if file_type == "pdf":
            for page in doc.get("pages", []):
                # Full page text (good for months; may be garbled for projects)
                if page.get("text"):
                    parts.append(page["text"])
                # All table cells — project names appear as column headers in values
                for table in page.get("tables", []):
                    for row in table:
                        for cell in row:
                            if isinstance(cell, str) and cell.strip():
                                parts.append(cell)

        elif file_type == "excel":
            for sheet in doc.get("sheets", []):
                parts.append(sheet.get("sheet_name", ""))
                for record in sheet.get("records", []):
                    if not record:
                        continue
                    for val in record.values():
                        if isinstance(val, str) and val.strip():
                            parts.append(val)

    return " ".join(parts)


def normalize_financial_data(parsed_result: dict) -> dict:
    """
    Transform a parsed document result into the canonical PnL / cashflow model.
    """
    analysis_id = parsed_result.get("analysis_id", "unknown")

    normalized: dict = {
        "analysis_id":      analysis_id,
        "status":           "success",
        "detected_reports": [],
        "period": {
            "period_label": None,   # e.g. "за 1 квартал 2026 года"
            "months":       [],     # e.g. ["2026-01", "2026-02", "2026-03"]
        },
        "pnl":              _empty_pnl(),
        "cashflow":         _empty_cashflow(),
        "projects":          [],
        "periods":           [],
        "projects_detected": [],
        "unmapped_lines":   [],
        "warnings":         [],
        "source_files":     [],
        "parsed_file_path": parsed_result.get("parsed_file_path", ""),
        "normalized_file_path": str(
            Path(parsed_result.get("parsed_file_path", "data/parsed/unknown"))
            .parent / f"{analysis_id}_normalized.json"
        ),
    }

    # Period and project detection — runs before the main loop so _n_months
    # is available for monthly cashflow extraction inside the doc loop.
    all_text        = _collect_text_from_parsed(parsed_result)
    detected_months = detect_months_from_text(all_text)
    period_info     = detect_period_label(detected_months)
    normalized["period"].update(period_info)
    normalized["projects_detected"] = detect_projects_from_text(all_text)

    _period_months: list[str] = normalized["period"].get("months") or []
    _n_months = len(_period_months)

    for doc in parsed_result.get("documents", []):
        if doc.get("status") == "error":
            normalized["warnings"].append(
                f"Пропущен файл с ошибкой: {doc.get('original_filename', '')}"
            )
            continue

        fname = doc.get("original_filename", "")
        if fname and fname not in normalized["source_files"]:
            normalized["source_files"].append(fname)

        rows = extract_rows_from_document(doc)
        if not rows:
            normalized["warnings"].append(
                f"В «{fname}» не найдено строк для нормализации"
            )
            continue

        labels = [r["label"] for r in rows]
        report_type = detect_report_type(labels)

        if report_type == "mixed":
            for rt in ("pnl", "cashflow"):
                if rt not in normalized["detected_reports"]:
                    normalized["detected_reports"].append(rt)
        elif report_type not in normalized["detected_reports"]:
            normalized["detected_reports"].append(report_type)

        # Two-direction pending state for cashflow-detail pairing.
        #
        # In some PDFs the numeric value row comes BEFORE the sub-line label (common):
        #   "-120606.61 ... -409378.71"         ← pending_numeric set here
        #   "·· IT, связь, сервисы 4.16% ..."  ← consumed here
        #
        # In other PDFs the label comes first, value follows (fallback):
        #   "·· Заработная плата"               ← pending_detail_key set here
        #   "-1597809.68 ... -5516065.51"        ← consumed here
        #
        # Percentage-only rows between the pair are transparent (do not reset pending).
        pending_numeric:    float | None = None   # last numeric-only value
        pending_detail_key: str   | None = None   # sub-line key waiting for a numeric row

        for row in rows:
            label  = row["label"].strip()
            values = row["values"]

            norm_label = _normalize_text(label)
            if not norm_label or _is_skip_label(norm_label):
                pending_numeric = None
                pending_detail_key = None
                continue
            if _is_ignorable_label(norm_label, values):
                pending_numeric = None
                pending_detail_key = None
                continue

            # Percentage-only rows are display artefacts that appear between numeric
            # lines and sub-line labels.  Skip without disturbing pending state so
            # adjacent pairs still resolve correctly.
            if _is_percentage_only_line(label):
                continue

            # ── Case A: pending_detail_key → resolve on next numeric-only row ──────
            if pending_detail_key is not None:
                if _is_numbers_only(norm_label):
                    embedded = _extract_numbers_from_string(label)
                    if embedded:
                        _assign_detail(normalized, pending_detail_key, embedded[-1])
                    pending_detail_key = None
                    pending_numeric = None
                    continue
                # Non-numeric, non-% row → stale key; fall through to normal processing
                pending_detail_key = None

            is_subline = _is_cashflow_subline(label)

            # ── Sub-lines (·· ···) → cashflow_details ────────────────────────────
            if is_subline and report_type in ("cashflow", "mixed"):
                detail_field = _map_to_cashflow_detail(label)
                if detail_field is not None:
                    main_value = _pick_value_from_row(values)
                    if main_value is None:
                        # Try numbers embedded in the label itself (e.g. "··· НДФЛ -672764")
                        embedded = _extract_numbers_from_string(label)
                        if embedded:
                            main_value = embedded[-1]
                    if main_value is not None:
                        _assign_detail(normalized, detail_field, main_value)
                        pending_numeric = None  # consumed / redundant
                    elif pending_numeric is not None:
                        # Case B: numeric row came BEFORE this sub-line label
                        _assign_detail(normalized, detail_field, pending_numeric)
                        pending_numeric = None
                    else:
                        # Case A: wait for numeric row that follows this label
                        pending_detail_key = detail_field
                elif label not in normalized["unmapped_lines"]:
                    normalized["unmapped_lines"].append(label)
                continue

            canonical = map_raw_line_to_canonical(label)

            # Safety net: aggregate sub-lines must not overwrite top-level totals
            # (primarily for non-cashflow documents or single-bullet lines).
            if canonical in _CASHFLOW_AGGREGATE_FIELDS and is_subline:
                canonical = None

            # Additional guard for operating_outflows: exclude rows that describe
            # a specific sub-category rather than the overall outflows total.
            if canonical == "operating_outflows":
                if any(excl in norm_label for excl in _OUTFLOWS_EXCLUSIONS):
                    canonical = None

            if canonical is None:
                if _is_numbers_only(norm_label) and report_type in ("cashflow", "mixed"):
                    embedded = _extract_numbers_from_string(label)
                    if embedded:
                        # Heuristic: first all-positive numeric-only row → operating inflows
                        if (normalized["cashflow"]["operating_inflows"] is None
                                and len(embedded) > 1
                                and all(n >= 0 for n in embedded)):
                            normalized["cashflow"]["operating_inflows"] = embedded[-1]
                            normalized["cashflow"]["operating_inflows_source"] = "heuristic"
                        # Save the last value as pending for the next sub-line (Case B)
                        pending_numeric = embedded[-1]
                        continue   # don't add to unmapped — might be consumed
                # Non-numeric, non-sub-line row clears numeric pending
                pending_numeric = None
                if label not in normalized["unmapped_lines"]:
                    normalized["unmapped_lines"].append(label)
                continue

            # Canonical row with a known field mapping
            pending_numeric = None  # non-sub-line canonical row breaks pending chain
            main_value = _pick_value_from_row(values)
            if main_value is None:
                # Fallback: numbers embedded in label
                # (PDF text-mode rows have values=[] because the whole line is the label)
                embedded = _extract_numbers_from_string(label)
                if embedded:
                    main_value = embedded[-1]
            _assign(normalized, canonical, main_value)

        # Monthly cashflow extraction for cashflow/mixed documents (last write wins)
        if report_type in ("cashflow", "mixed") and _n_months > 0:
            _monthly = _extract_monthly_cashflow(
                rows, _n_months, _period_months, normalized["warnings"]
            )
            for _mk, _md in _monthly.items():
                if _mk not in normalized["cashflow"]["monthly"]:
                    normalized["cashflow"]["monthly"][_mk] = {}
                normalized["cashflow"]["monthly"][_mk].update(_md)

        # PnL matrix extraction for pnl/mixed documents (monthly + project breakdown)
        if report_type in ("pnl", "mixed") and _n_months > 0 and normalized["projects_detected"]:
            _pnl_matrix = extract_pnl_matrix_from_rows(
                rows, normalized["period"], normalized["projects_detected"]
            )
            _matrix_warnings = _pnl_matrix.pop("_warnings", [])
            normalized["warnings"].extend(_matrix_warnings)

            _pnl = normalized["pnl"]
            for _mk, _mdata in _pnl_matrix.get("monthly", {}).items():
                _pnl["monthly"].setdefault(_mk, {}).update(_mdata)

            for _proj, _pdata in _pnl_matrix.get("projects", {}).items():
                _pnl["projects"].setdefault(_proj, {}).update(_pdata)

            for _proj, _pm in _pnl_matrix.get("project_monthly", {}).items():
                _proj_entry = _pnl["project_monthly"].setdefault(_proj, {})
                for _mk, _mdata in _pm.items():
                    _proj_entry.setdefault(_mk, {}).update(_mdata)

            _pnl["period_totals"].update(_pnl_matrix.get("period_totals", {}))

    _apply_derived_logic(normalized)

    if _n_months > 0 and not normalized["cashflow"]["monthly"]:
        normalized["warnings"].append(
            "Отчётный период определён, но помесячные значения ДДС не извлечены."
        )

    if normalized["projects_detected"] and not normalized["pnl"]["projects"]:
        normalized["warnings"].append(
            "Проекты определены, но БДР не удалось разложить по проектам."
        )

    if _n_months > 0 and "pnl" in normalized["detected_reports"] and not normalized["pnl"]["monthly"]:
        normalized["warnings"].append(
            "Месяцы определены, но БДР не удалось разложить по месяцам."
        )

    if not normalized["detected_reports"]:
        normalized["detected_reports"] = ["unknown"]
        normalized["warnings"].append(
            "Не удалось определить тип отчёта (БДР или ДДС). "
            "Проверьте структуру файла."
        )

    normalized["quality"] = check_data_quality(normalized)
    normalized["kpi"]     = calculate_owner_kpi(normalized)
    analysis_ctx = build_analysis_context(normalized)  # saves JSON; sets analysis_context_path

    _report = generate_owner_report(analysis_ctx)
    normalized["owner_report"]       = _report.get("report_markdown", "")
    normalized["owner_report_error"] = _report.get("error")
    normalized["owner_report_path"]  = _report.get("saved_path", "")

    return normalized


def save_normalized_result(normalized: dict) -> str:
    out_path = Path(normalized["normalized_file_path"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return str(out_path)
