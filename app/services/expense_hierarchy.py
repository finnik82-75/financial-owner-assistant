"""
Expense hierarchy classification for PNL (БДР) and cashflow (ДДС) lines.

Classifies financial statement rows so expense-share calculations
never mix group totals, calculated metrics, or non-operating items
with leaf-level expense lines.
"""
from __future__ import annotations

# ─── Russian label maps ───────────────────────────────────────────────────────

PNL_LABEL_MAP: dict[str, str] = {
    "revenue":                     "Выручка",
    "cogs":                        "Себестоимость",
    "gross_profit":                "Валовая прибыль",
    "operating_expenses":          "Операционные расходы",
    "payroll":                     "ФОТ",
    "payroll_related":             "ФОТ-связанные расходы",
    "rent":                        "Аренда",
    "marketing":                   "Маркетинг",
    "bank_fees":                   "Банковские комиссии",
    "communication":               "Связь",
    "legal_services":              "Юридические услуги",
    "it_expenses":                 "ИТ-расходы",
    "depreciation":                "Амортизация",
    "other_operating_expenses":    "Прочие операционные расходы",
    "taxes":                       "Налоги",
    "intercompany_turnover":       "ВГО",
    "intercompany_services":       "ВГО-услуги",
    "intercompany_interest":       "ВГО-проценты по займу",
    "operating_profit":            "Прибыль от основной деятельности",
    "profit_after_other_activity": "Прибыль с учётом прочей деятельности",
    "ebitda_proxy":                "EBITDA (proxy)",
    "net_profit":                  "Чистая прибыль",
    "net_profit_proxy":            "Чистая прибыль (proxy)",
}

CF_DETAIL_LABEL_MAP: dict[str, str] = {
    "customer_inflows":              "Поступления от клиентов",
    "media_services_inflows":        "Поступления за медиауслуги",
    "intercompany_operating_inflows": "ВГО-поступления",
    "payroll":                       "Заработная плата",
    "contractors":                   "Оплата подрядчикам",
    "employee_taxes":                "Налоги за сотрудников",
    "social_contributions":          "Взносы в фонды",
    "personal_income_tax":           "НДФЛ",
    "income_tax":                    "Налоги на доходы",
    "it_communication_services":     "IT, связь, сервисы",
    "bank_fees":                     "Банковские комиссии",
    "marketing":                     "Маркетинг и реклама",
    "materials":                     "Оплата за ТМЦ/материалы",
    "other_operating_outflows":      "Прочие выплаты",
    "intercompany_financing":        "ВГО-финансирование",
    "operating_inflows":             "Поступления (операционные)",
    "operating_outflows":            "Выплаты (операционные)",
    "operating_cashflow":            "Операционный денежный поток",
    "investment_cashflow":           "Инвестиционный поток",
    "financial_cashflow":            "Финансовый поток",
    "net_cashflow":                  "Чистый денежный поток",
    "cash_start":                    "Остаток на начало",
    "cash_end":                      "Остаток на конец",
}

# ─── PNL classification ───────────────────────────────────────────────────────
# level meanings:
#   non_expense      — не расход (выручка)
#   group_total      — итоговая группа (включает несколько leaf-строк ниже)
#   calculated_metric — расчётный показатель (прибыль, EBITDA, маржа)
#   non_operating    — внереализационная / финансовая (ВГО, займы)
#   leaf_item        — конечная статья расходов, подходит для долей

_PNL_CLASSIFICATION: dict[str, dict] = {
    "revenue": {
        "level": "non_expense",
        "include_in_expense_share": False,
        "reason": "Выручка — не расход",
    },
    "cogs": {
        "level": "group_total",
        "include_in_expense_share": False,
        "reason": "Себестоимость — отдельная группа, не смешивается с операционными расходами",
    },
    "gross_profit": {
        "level": "calculated_metric",
        "include_in_expense_share": False,
        "reason": "Валовая прибыль — расчётный показатель (выручка - себестоимость)",
    },
    "operating_expenses": {
        "level": "group_total",
        "include_in_expense_share": False,
        "reason": "Операционные расходы — итоговая группа; используется как база, не как строка",
    },
    "intercompany_turnover": {
        "level": "non_operating",
        "include_in_expense_share": False,
        "reason": "ВГО — внутригрупповой оборот, не относится к операционным расходам",
    },
    "intercompany_services": {
        "level": "non_operating",
        "include_in_expense_share": False,
        "reason": "ВГО-услуги — внутригрупповые, не относятся к операционным расходам",
    },
    "intercompany_interest": {
        "level": "non_operating",
        "include_in_expense_share": False,
        "reason": "ВГО-проценты по займу — финансовая деятельность, не операционные расходы",
    },
    "operating_profit": {
        "level": "calculated_metric",
        "include_in_expense_share": False,
        "reason": "Прибыль от основной деятельности — расчётный показатель",
    },
    "profit_after_other_activity": {
        "level": "calculated_metric",
        "include_in_expense_share": False,
        "reason": "Прибыль с учётом прочей деятельности — расчётный показатель",
    },
    "ebitda_proxy": {
        "level": "calculated_metric",
        "include_in_expense_share": False,
        "reason": "EBITDA proxy — расчётный показатель",
    },
    "net_profit": {
        "level": "calculated_metric",
        "include_in_expense_share": False,
        "reason": "Чистая прибыль — расчётный показатель",
    },
    "net_profit_proxy": {
        "level": "calculated_metric",
        "include_in_expense_share": False,
        "reason": "Чистая прибыль proxy — расчётный показатель",
    },
    # ── Leaf items — include in expense share ─────────────────────────────────
    "payroll": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "ФОТ — конечная статья операционных расходов",
    },
    "payroll_related": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "ФОТ-связанные расходы — конечная статья",
    },
    "rent": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "Аренда — конечная статья операционных расходов",
    },
    "marketing": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "Маркетинг — конечная статья операционных расходов",
    },
    "bank_fees": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "Банковские комиссии — конечная статья операционных расходов",
    },
    "communication": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "Связь — конечная статья операционных расходов",
    },
    "legal_services": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "Юридические услуги — конечная статья операционных расходов",
    },
    "it_expenses": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "ИТ-расходы — конечная статья операционных расходов",
    },
    "depreciation": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "Амортизация — конечная статья операционных расходов",
    },
    "taxes": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "Налоги — конечная статья операционных расходов",
    },
    "other_operating_expenses": {
        "level": "leaf_item",
        "include_in_expense_share": True,
        "reason": "Прочие операционные расходы — остаточная конечная статья (без детализации)",
    },
}


# ─── Cashflow classification ──────────────────────────────────────────────────

_CF_CLASSIFICATION: dict[str, dict] = {
    # Group totals — use as base, never as line items
    "operating_inflows": {
        "level": "group_total",
        "include_in_outflow_share": False,
        "cashflow_type": "total",
        "reason": "Итог поступлений — используется как база, не как строка",
    },
    "operating_outflows": {
        "level": "group_total",
        "include_in_outflow_share": False,
        "cashflow_type": "total",
        "reason": "Итог выплат — используется как база для долей, не как строка",
    },
    "operating_cashflow": {
        "level": "calculated_metric",
        "include_in_outflow_share": False,
        "cashflow_type": "total",
        "reason": "Операционный денежный поток — расчётный показатель",
    },
    "investment_cashflow": {
        "level": "calculated_metric",
        "include_in_outflow_share": False,
        "cashflow_type": "total",
        "reason": "Инвестиционный поток — не смешивать с операционными выплатами",
    },
    "financial_cashflow": {
        "level": "calculated_metric",
        "include_in_outflow_share": False,
        "cashflow_type": "total",
        "reason": "Финансовый поток — не смешивать с операционными выплатами",
    },
    "net_cashflow": {
        "level": "calculated_metric",
        "include_in_outflow_share": False,
        "cashflow_type": "total",
        "reason": "Чистый денежный поток — расчётный показатель",
    },
    "cash_start": {
        "level": "non_expense",
        "include_in_outflow_share": False,
        "cashflow_type": "balance",
        "reason": "Остаток на начало — не выплата",
    },
    "cash_end": {
        "level": "non_expense",
        "include_in_outflow_share": False,
        "cashflow_type": "balance",
        "reason": "Остаток на конец — не выплата",
    },
    # Inflows — separate from outflows
    "customer_inflows": {
        "level": "leaf_item",
        "include_in_outflow_share": False,
        "cashflow_type": "inflow",
        "reason": "Поступления от клиентов — приход, не операционная выплата",
    },
    "media_services_inflows": {
        "level": "leaf_item",
        "include_in_outflow_share": False,
        "cashflow_type": "inflow",
        "reason": "Поступления за медиауслуги — приход, не операционная выплата",
    },
    "intercompany_operating_inflows": {
        "level": "leaf_item",
        "include_in_outflow_share": False,
        "cashflow_type": "inflow",
        "reason": "ВГО-поступления — приход, не операционная выплата",
    },
    # Financing — separate from operating outflows
    "intercompany_financing": {
        "level": "non_operating",
        "include_in_outflow_share": False,
        "cashflow_type": "financing",
        "reason": "ВГО-финансирование — финансовая деятельность, не операционная выплата",
    },
    # Operating outflow leaf items — include in share
    "payroll": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "Заработная плата — операционная выплата",
    },
    "contractors": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "Оплата подрядчикам — операционная выплата",
    },
    "employee_taxes": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "Налоги за сотрудников — операционная выплата",
    },
    "social_contributions": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "Взносы в фонды — операционная выплата",
    },
    "personal_income_tax": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "НДФЛ — операционная выплата",
    },
    "income_tax": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "Налоги на доходы — операционная выплата",
    },
    "it_communication_services": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "IT, связь, сервисы — операционная выплата",
    },
    "bank_fees": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "Банковские комиссии — операционная выплата",
    },
    "marketing": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "Маркетинг и реклама — операционная выплата",
    },
    "materials": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "Оплата за ТМЦ/материалы — операционная выплата",
    },
    "other_operating_outflows": {
        "level": "leaf_item",
        "include_in_outflow_share": True,
        "cashflow_type": "outflow",
        "reason": "Прочие выплаты — остаточная статья операционных выплат",
    },
}


# ─── Public API ───────────────────────────────────────────────────────────────

def classify_pnl_expense_line(metric_key: str, label: str | None = None) -> dict:
    """
    Classify a PNL (БДР) row for expense-share calculation.

    Returns:
        {metric_key, label, level, include_in_expense_share, reason}
    """
    resolved_label = label or PNL_LABEL_MAP.get(metric_key, metric_key)
    cls = _PNL_CLASSIFICATION.get(metric_key)
    if cls:
        return {
            "metric_key":               metric_key,
            "label":                    resolved_label,
            "level":                    cls["level"],
            "include_in_expense_share": cls["include_in_expense_share"],
            "reason":                   cls["reason"],
        }
    return {
        "metric_key":               metric_key,
        "label":                    resolved_label,
        "level":                    "unknown",
        "include_in_expense_share": False,
        "reason":                   "Неизвестный ключ — исключён как неклассифицированный",
    }


def classify_cashflow_detail_line(metric_key: str, label: str | None = None) -> dict:
    """
    Classify a cashflow (ДДС) detail row for outflow-share calculation.

    Returns:
        {metric_key, label, level, include_in_outflow_share, cashflow_type, reason}
    """
    resolved_label = label or CF_DETAIL_LABEL_MAP.get(metric_key, metric_key)
    cls = _CF_CLASSIFICATION.get(metric_key)
    if cls:
        return {
            "metric_key":               metric_key,
            "label":                    resolved_label,
            "level":                    cls["level"],
            "include_in_outflow_share": cls["include_in_outflow_share"],
            "cashflow_type":            cls.get("cashflow_type", "unknown"),
            "reason":                   cls["reason"],
        }
    return {
        "metric_key":               metric_key,
        "label":                    resolved_label,
        "level":                    "unknown",
        "include_in_outflow_share": False,
        "cashflow_type":            "unknown",
        "reason":                   "Неизвестный ключ — исключён как неклассифицированный",
    }
