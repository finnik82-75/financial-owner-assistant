"""
Build a self-contained analysis_context dict for future LLM report generation.

No LLM is used here. All data is structured, calculated, and formatted by Python.
The resulting JSON is saved to data/outputs/{analysis_id}_analysis_context.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import settings

# ─── Label dicts (local copy — avoids circular import with normalizer) ────────

_PNL_TOTAL_KEYS: list[str] = [
    "revenue", "cogs", "gross_profit", "operating_expenses", "payroll",
    "payroll_related", "rent", "marketing", "bank_fees", "communication",
    "legal_services", "it_expenses", "depreciation", "other_operating_expenses",
    "taxes", "intercompany_turnover", "intercompany_services", "intercompany_interest",
    "operating_profit", "profit_after_other_activity", "ebitda_proxy",
    "net_profit", "net_profit_proxy",
]

_CF_TOTAL_KEYS: list[str] = [
    "cash_start", "operating_inflows", "operating_outflows", "operating_cashflow",
    "investment_cashflow", "financial_cashflow", "net_cashflow", "cash_end",
    "owner_withdrawals", "advances",
]

_CF_DETAIL_LABELS: dict[str, str] = {
    "customer_inflows":               "Поступления от клиентов",
    "media_services_inflows":         "Поступления от медиауслуг",
    "intercompany_operating_inflows": "ВГО (операционная деятельность)",
    "intercompany_financing":         "ВГО (финансирование)",
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
}


# ─── Internal format helpers ──────────────────────────────────────────────────

def _rub(v: float | None) -> str:
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}{abs(v):,.0f}".replace(",", " ")


# ─── Section builders ─────────────────────────────────────────────────────────

def _build_business_summary(normalized_data: dict) -> dict:
    period = normalized_data.get("period") or {}
    pnl    = normalized_data.get("pnl") or {}
    cf     = normalized_data.get("cashflow") or {}

    op_profit = pnl.get("operating_profit")
    op_cf     = cf.get("operating_cashflow")
    net_cf    = cf.get("net_cashflow")
    cash_end  = cf.get("cash_end")
    revenue   = pnl.get("revenue")

    if op_profit is not None and op_cf is not None:
        if op_profit < 0 and op_cf < 0:
            main_result = "Бизнес убыточен по БДР и одновременно сжигает деньги операционно."
        elif op_profit > 0 and op_cf < 0:
            main_result = "Бизнес прибыльный по БДР, но прибыль не превращается в деньги."
        elif op_profit < 0 and op_cf > 0:
            main_result = "Деньги есть, но не за счет операционной прибыли."
        else:
            main_result = "Бизнес прибыльный и генерирует операционный денежный поток."
    elif op_profit is not None:
        main_result = ("Операционная деятельность убыточна." if op_profit < 0
                       else "Операционная деятельность прибыльна.")
    elif op_cf is not None:
        main_result = ("Операционный поток отрицательный." if op_cf < 0
                       else "Операционный поток положительный.")
    else:
        main_result = "Недостаточно данных для итоговой оценки."

    return {
        "period_label":       period.get("period_label") or "—",
        "revenue":            revenue,
        "operating_profit":   op_profit,
        "operating_cashflow": op_cf,
        "net_cashflow":       net_cf,
        "cash_end":           cash_end,
        "main_result":        main_result,
    }


def _build_key_findings(normalized_data: dict) -> list[str]:
    findings: list[str] = []
    period      = normalized_data.get("period") or {}
    pnl         = normalized_data.get("pnl") or {}
    cf          = normalized_data.get("cashflow") or {}
    period_label = period.get("period_label") or ""
    months_list  = period.get("months") or []
    month_names  = period.get("month_names") or []
    name_map     = dict(zip(months_list, month_names))

    revenue   = pnl.get("revenue")
    op_profit = pnl.get("operating_profit")
    op_cf     = cf.get("operating_cashflow")
    fin_cf    = cf.get("financial_cashflow")

    # 1. Revenue
    if revenue is not None:
        prefix = period_label.capitalize() if period_label else ""
        sep    = " " if prefix else ""
        findings.append(f"{prefix}{sep}выручка составила {_rub(revenue)} ₽.")

    # 2. Operating profit
    if op_profit is not None:
        if op_profit < 0:
            findings.append(
                f"Прибыль от основной деятельности отрицательная: {_rub(op_profit)} ₽."
            )
        else:
            findings.append(
                f"Прибыль от основной деятельности: +{_rub(op_profit)} ₽."
            )

    has_cashflow = bool(cf.get("totals"))

    # 3. Operating cashflow
    if has_cashflow and op_cf is not None:
        if op_cf < 0:
            findings.append(f"Операционный денежный поток отрицательный: {_rub(op_cf)} ₽.")
        else:
            findings.append(f"Операционный денежный поток положительный: +{_rub(op_cf)} ₽.")

    # 4. Financial cashflow compensating operational deficit
    if (has_cashflow and fin_cf is not None and fin_cf > 0
            and op_profit is not None and op_profit < 0):
        findings.append(
            f"Финансовый поток {_rub(fin_cf)} ₽ почти перекрыл операционный денежный минус."
        )

    # 5. Worst month by operating profit
    pnl_monthly = pnl.get("monthly") or {}
    month_profits = {
        mk: m.get("operating_profit")
        for mk, m in pnl_monthly.items()
        if m.get("operating_profit") is not None
    }
    if month_profits:
        worst_mk     = min(month_profits, key=lambda k: month_profits[k])
        worst_profit = month_profits[worst_mk]
        worst_name   = name_map.get(worst_mk, worst_mk).capitalize()
        findings.append(
            f"Самый слабый месяц по прибыли — {worst_name}: {_rub(worst_profit)} ₽."
        )

    # 6. Largest cashflow detail expense
    cf_details = cf.get("cashflow_details") or {}
    if has_cashflow and cf_details:
        expenses = {k: v for k, v in cf_details.items() if v is not None and v < 0}
        if expenses:
            biggest_key = min(expenses, key=lambda k: expenses[k])
            biggest_val = expenses[biggest_key]
            label       = _CF_DETAIL_LABELS.get(biggest_key, biggest_key)
            findings.append(
                f"Крупнейшая статья ДДС-расходов — {label}: {_rub(biggest_val)} ₽."
            )

    # 7-8. Loss-making and profitable projects
    pnl_projects = pnl.get("projects") or {}
    if pnl_projects:
        loss_making = sorted(
            [(proj, d.get("operating_profit") or 0)
             for proj, d in pnl_projects.items()
             if (d.get("operating_profit") or 0) < 0],
            key=lambda x: x[1],
        )
        profitable = sorted(
            [(proj, d.get("operating_profit") or 0)
             for proj, d in pnl_projects.items()
             if (d.get("operating_profit") or 0) > 0],
            key=lambda x: -x[1],
        )
        if loss_making:
            names = ", ".join(p[0] for p in loss_making)
            findings.append(f'Главные убыточные проекты: {names}.')
        if profitable:
            names = ", ".join(p[0] for p in profitable)
            findings.append(f'Прибыльные проекты: {names}.')

    # 9. Break-even gap finding (uses pre-calculated KPI)
    kpi = normalized_data.get("kpi") or {}
    be_gap_entry = (kpi.get("summary_kpi") or {}).get("break_even_gap")
    if be_gap_entry and be_gap_entry.get("value") is not None:
        gap_val = float(be_gap_entry["value"])
        if gap_val < 0:
            findings.append(
                f"Фактическая выручка ниже расчётной точки безубыточности "
                f"на {_rub(abs(gap_val))} ₽."
            )
        else:
            findings.append(
                f"Фактическая выручка выше расчётной точки безубыточности "
                f"на {_rub(gap_val)} ₽."
            )

    return findings


def _build_report_constraints(normalized_data: dict) -> list[str]:
    constraints: list[str] = []
    pnl     = normalized_data.get("pnl") or {}
    cf      = normalized_data.get("cashflow") or {}
    quality = normalized_data.get("quality") or {}

    if pnl.get("uses_net_profit_proxy"):
        constraints.append(
            "Чистая прибыль не найдена. "
            "Используется proxy на основе прибыли от основной деятельности."
        )

    calculated: list[str] = []
    if pnl.get("gross_profit_calculated"):
        calculated.append("валовая прибыль")
    if pnl.get("operating_profit_calculated"):
        calculated.append("операционная прибыль")
    if cf.get("net_cashflow_calculated"):
        calculated.append("чистый денежный поток")
    if cf.get("cash_start_calculated"):
        calculated.append("остаток на начало")
    if cf.get("operating_outflows_source") == "calculated_from_inflows_and_operating_cashflow":
        calculated.append("операционные выплаты")
    if calculated:
        fields_str = ", ".join(calculated)
        constraints.append(
            f"Часть показателей рассчитана системой, а не извлечена напрямую "
            f"из отчёта: {fields_str}."
        )

    if quality.get("status") not in ("good", None, ""):
        for w in quality.get("warnings") or []:
            constraints.append(w)

    # Break-even proxy constraint (only if computed)
    kpi = normalized_data.get("kpi") or {}
    be_rev_entry = (kpi.get("summary_kpi") or {}).get("break_even_revenue")
    if be_rev_entry and be_rev_entry.get("value") is not None:
        constraints.append(
            "Точка безубыточности рассчитана как proxy: операционные расходы условно приняты "
            "как расходы, которые должны покрываться валовой прибылью. "
            "Расходы не разделены на постоянные и переменные."
        )

    return constraints


def _build_kb_section() -> dict:
    try:
        from app.services.knowledge_base_service import load_manifest
        manifest = load_manifest()
        version  = manifest.get("version", "unknown")
    except Exception:
        version = "unknown"
    return {
        "version":              version,
        "report_template_name": "owner_report_template",
        "language":             "ru",
    }


def _pnl_totals(pnl: dict) -> dict:
    return {k: pnl.get(k) for k in _PNL_TOTAL_KEYS if pnl.get(k) is not None}


def _cf_totals(cf: dict) -> dict:
    return {k: cf.get(k) for k in _CF_TOTAL_KEYS if cf.get(k) is not None}


# ─── Public API ───────────────────────────────────────────────────────────────

def build_analysis_context(normalized_data: dict) -> dict:
    """
    Build a self-contained analysis context from normalized financial data.

    Saves the result to data/outputs/{analysis_id}_analysis_context.json and
    sets normalized_data["analysis_context_path"] to the saved file path.

    Returns the context dict.
    """
    analysis_id = normalized_data.get("analysis_id", "unknown")
    pnl         = normalized_data.get("pnl") or {}
    cf          = normalized_data.get("cashflow") or {}
    kpi         = normalized_data.get("kpi") or {}
    quality     = normalized_data.get("quality") or {}

    ctx: dict = {
        "analysis_id":      analysis_id,
        "period":           normalized_data.get("period") or {},
        "source_files":     normalized_data.get("source_files") or [],
        "detected_reports": normalized_data.get("detected_reports") or [],
        "data_quality": {
            "status":          quality.get("status"),
            "score":           quality.get("score"),
            "critical_issues": quality.get("critical_issues") or [],
            "warnings":        quality.get("warnings") or [],
            "notes":           quality.get("notes") or [],
        },
        "business_summary": _build_business_summary(normalized_data),
        "pnl": {
            "totals":         _pnl_totals(pnl),
            "monthly":        pnl.get("monthly") or {},
            "projects":       pnl.get("projects") or {},
            "project_monthly": pnl.get("project_monthly") or {},
        },
        "cashflow": {
            "totals":  _cf_totals(cf),
            "monthly": cf.get("monthly") or {},
            "details": cf.get("cashflow_details") or {},
        },
        "kpi": {
            "period_label": kpi.get("period_label") or "—",
            "summary_kpi":  kpi.get("summary_kpi") or {},
            "monthly_kpi":  kpi.get("monthly_kpi") or {},
            "project_kpi":  kpi.get("project_kpi") or {},
        },
        "key_findings":      _build_key_findings(normalized_data),
        "report_constraints": _build_report_constraints(normalized_data),
        "knowledge_base":    _build_kb_section(),
    }

    # Save to data/outputs/
    out_path = settings.output_dir / f"{analysis_id}_analysis_context.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(ctx, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    normalized_data["analysis_context_path"] = str(out_path)
    return ctx
